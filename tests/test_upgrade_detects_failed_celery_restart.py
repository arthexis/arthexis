import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(os.name != "posix", reason="requires POSIX-compatible shell")


def _make_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | 0o111)


def test_upgrade_fails_when_celery_units_stay_inactive(tmp_path: Path) -> None:
    clone_path = tmp_path / "arthexis-clone"
    subprocess.run(
        ["git", "clone", "--depth", "1", str(REPO_ROOT), str(clone_path)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    clone_path.joinpath("upgrade.sh").write_text(
        (REPO_ROOT / "upgrade.sh").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    (clone_path / "VERSION").write_text("0.0.0\n", encoding="utf-8")

    # Prepare minimal environment expected by upgrade.sh
    (clone_path / "locks").mkdir(parents=True, exist_ok=True)
    (clone_path / "locks" / "service.lck").write_text("arthexis\n", encoding="utf-8")
    (clone_path / "locks" / "celery.lck").write_text("enabled\n", encoding="utf-8")

    (clone_path / ".venv" / "bin").mkdir(parents=True, exist_ok=True)
    _make_executable(clone_path / ".venv" / "bin" / "python", "#!/usr/bin/env bash\nexit 0\n")

    # Replace scripts invoked during the upgrade with deterministic stubs
    _make_executable(clone_path / "env-refresh.sh", "#!/usr/bin/env bash\nexit 0\n")
    _make_executable(clone_path / "stop.sh", "#!/usr/bin/env bash\nexit 0\n")
    _make_executable(clone_path / "start.sh", "#!/usr/bin/env bash\nexit 0\n")

    # Provide stub versions of systemctl and sudo to simulate inactive Celery services
    systemctl_stub = "\n".join(
        [
            "#!/usr/bin/env bash",
            "set -e",
            "if [ \"$1\" = \"list-unit-files\" ]; then",
            "  cat <<'EOF'",
            "arthexis.service                                 enabled",
            "celery-arthexis.service                          enabled",
            "celery-beat-arthexis.service                     enabled",
            "EOF",
            "  exit 0",
            "fi",
            "if [ \"$1\" = \"restart\" ]; then",
            "  exit 0",
            "fi",
            "if [ \"$1\" = \"is-active\" ]; then",
            "  shift",
            "  while [ \"$1\" = \"--quiet\" ]; do",
            "    shift",
            "  done",
            "  unit=$1",
            "  case $unit in",
            "    arthexis)",
            "      echo active",
            "      exit 0",
            "      ;;",
            "    celery-arthexis|celery-beat-arthexis)",
            "      echo inactive",
            "      exit 3",
            "      ;;",
            "  esac",
            "fi",
            "if [ \"$1\" = \"status\" ]; then",
            "  unit=$2",
            "  shift 2",
            "  while [ $# -gt 0 ] && [ \"$1\" = \"--no-pager\" ]; do",
            "    shift",
            "  done",
            "  if [[ $unit == celery-* ]]; then",
            "    echo \"$unit is inactive\"",
            "    exit 3",
            "  fi",
            "  echo \"$unit is running\"",
            "  exit 0",
            "fi",
            "echo \"systemctl stub: $*\" >&2",
            "exit 0",
        ]
    )
    _make_executable(clone_path / "systemctl", systemctl_stub + "\n")

    sudo_stub = "\n".join(
        [
            "#!/usr/bin/env bash",
            "if [ \"$1\" = \"-n\" ]; then",
            "  shift",
            "fi",
            'exec "$@"',
        ]
    )
    _make_executable(clone_path / "sudo", sudo_stub + "\n")

    real_git = shutil.which("git")
    assert real_git is not None
    git_stub = "\n".join(
        [
            "#!/usr/bin/env bash",
            "if [ \"$1\" = \"pull\" ] && [ \"$2\" = \"--rebase\" ]; then",
            "  exit 0",
            "fi",
            f"exec {real_git} \"$@\"",
        ]
    )
    _make_executable(clone_path / "git", git_stub + "\n")

    env = os.environ.copy()
    env["PATH"] = f"{clone_path}:{env['PATH']}"
    env["ARTHEXIS_WAIT_FOR_ACTIVE_TIMEOUT"] = "2"

    result = subprocess.run(
        ["bash", "./upgrade.sh"],
        cwd=clone_path,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode != 0
    assert "Celery service celery-arthexis did not become active after restart." in result.stderr
