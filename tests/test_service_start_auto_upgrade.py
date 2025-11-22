import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(os.name != "posix", reason="requires POSIX-compatible shell")


def _make_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | 0o111)


def _prepare_service_start_tree(tmp_path: Path) -> tuple[Path, Path]:
    base = tmp_path / "service-start"
    base.mkdir()

    shutil.copy2(REPO_ROOT / "service-start.sh", base / "service-start.sh")
    shutil.copytree(REPO_ROOT / "scripts", base / "scripts")

    (base / "logs").mkdir()
    locks = base / "locks"
    locks.mkdir()

    upgrade_log = base / "upgrade.log"
    _make_executable(
        base / "upgrade.sh",
        "\n".join(
            [
                "#!/usr/bin/env bash",
                f"echo \"$@\" >> '{upgrade_log}'",
                "exit 0",
            ]
        ),
    )

    venv_bin = base / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    _make_executable(
        venv_bin / "activate",
        "\n".join([
            "#!/usr/bin/env bash",
            "VENV_DIR=\"$(cd \"$(dirname \"$BASH_SOURCE\")\" && pwd)\"",
            "export PATH=\"$VENV_DIR:$PATH\"",
        ]),
    )
    _make_executable(
        venv_bin / "python",
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "if [ -n \"${COMMAND_LOG:-}\" ]; then",
                "  echo \"python $@\" >> \"$COMMAND_LOG\"",
                "fi",
                "exit 0",
            ]
        ),
    )
    _make_executable(
        venv_bin / "celery",
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "if [ -n \"${COMMAND_LOG:-}\" ]; then",
                "  echo \"celery $@\" >> \"$COMMAND_LOG\"",
                "fi",
                "exit 0",
            ]
        ),
    )

    return base, upgrade_log


def _run_service_start(base: Path, *, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.setdefault("TZ", "UTC")
    env["PATH"] = f"{base / '.venv' / 'bin'}:{env['PATH']}"
    if extra_env:
        env.update(extra_env)

    return subprocess.run(
        ["bash", str(base / "service-start.sh"), "--no-celery", "--port", "9999"],
        check=False,
        capture_output=True,
        text=True,
        cwd=base,
        env=env,
    )


def test_service_start_runs_upgrade_with_mode(tmp_path: Path) -> None:
    base, upgrade_log = _prepare_service_start_tree(tmp_path)
    mode_file = base / "locks" / "auto_upgrade.lck"
    mode_file.write_text("LATEST\n", encoding="utf-8")

    result = _run_service_start(base)

    assert result.returncode == 0
    log_lines = upgrade_log.read_text(encoding="utf-8").splitlines()
    assert any("--no-restart" in line for line in log_lines)
    assert any("--latest" in line for line in log_lines)


def test_service_start_honors_skip_lock(tmp_path: Path) -> None:
    base, upgrade_log = _prepare_service_start_tree(tmp_path)
    mode_file = base / "locks" / "auto_upgrade.lck"
    mode_file.write_text("stable\n", encoding="utf-8")

    skip_lock = base / "locks" / "service-start-skip.lck"
    skip_lock.write_text("skip once\n", encoding="utf-8")
    now = time.time()
    os.utime(skip_lock, (now, now))

    result = _run_service_start(base)

    assert result.returncode == 0
    assert not upgrade_log.exists() or upgrade_log.read_text(encoding="utf-8").strip() == ""
    assert not skip_lock.exists()
