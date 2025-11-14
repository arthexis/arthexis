from __future__ import annotations

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


def test_upgrade_reports_active_sessions_without_force_hint(tmp_path: Path) -> None:
    clone_path = tmp_path / "arthexis-clone"
    subprocess.run(
        ["git", "clone", "--depth", "1", str(REPO_ROOT), str(clone_path)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    (clone_path / ".venv").mkdir(parents=True, exist_ok=True)

    log_file = clone_path / "stop-invocations.log"
    if log_file.exists():
        log_file.unlink()

    _make_executable(
        clone_path / "stop.sh",
        "#!/usr/bin/env bash\n"
        "LOG_FILE=\"$(cd \"$(dirname \"$0\")\" && pwd)/stop-invocations.log\"\n"
        "printf '%s\\n' \"$*\" >> \"$LOG_FILE\"\n"
        "exit 1\n",
    )

    # Ensure the local checkout appears outdated so the upgrade continues.
    (clone_path / "VERSION").write_text("0.0.0\n", encoding="utf-8")

    real_git = shutil.which("git")
    assert real_git is not None
    _make_executable(
        clone_path / "git",
        "#!/usr/bin/env bash\n"
        "if [ \"$1\" = \"pull\" ] && [ \"$2\" = \"--rebase\" ]; then\n"
        "  exit 0\n"
        "fi\n"
        f"exec {real_git} \"$@\"\n",
    )

    env = os.environ.copy()
    env["PATH"] = f"{clone_path}:{env['PATH']}"

    first_result = subprocess.run(
        ["bash", "./upgrade.sh", "--no-restart"],
        cwd=clone_path,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert first_result.returncode == 1
    assert (
        "Upgrade aborted because active charging sessions are in progress. Resolve active charging sessions before retrying." in first_result.stderr
    )
    assert "--force" not in first_result.stderr

    second_result = subprocess.run(
        ["bash", "./upgrade.sh", "--force", "--no-restart"],
        cwd=clone_path,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert second_result.returncode == 1
    assert (
        "Upgrade aborted even after forcing stop. Resolve active charging sessions before retrying." in second_result.stderr
    )

    assert log_file.exists()
    invocations = log_file.read_text(encoding="utf-8").splitlines()
    assert invocations == ["--all", "--all --force"]
