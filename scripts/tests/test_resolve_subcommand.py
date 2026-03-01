"""Regression tests for resolve entrypoint compatibility."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest


BASH = shutil.which("bash")


pytestmark = [
    pytest.mark.regression,
    pytest.mark.pr("PR-5652", "2026-02-26T00:00:00Z"),
    pytest.mark.skipif(BASH is None, reason="bash not found in PATH"),
]


REPO_ROOT = Path(__file__).resolve().parents[2]
LEGACY_ENTRYPOINT = REPO_ROOT / "resolve.sh"
NEW_ENTRYPOINT = [sys.executable, "-m", "arthexis", "resolve"]


@pytest.fixture
def fake_python(tmp_path: Path) -> Path:
    """Create a fake Python executable that captures argv and stdin."""
    fake_python_path = tmp_path / "fake-python"
    fake_python_path.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "printf 'ARGV:%s\\n' \"$*\"\n"
        "stdin_value=$(cat)\n"
        "printf 'STDIN:%s\\n' \"$stdin_value\"\n",
        encoding="utf-8",
    )
    fake_python_path.chmod(fake_python_path.stat().st_mode | stat.S_IEXEC)
    return fake_python_path


def _legacy_command() -> list[str]:
    """Return a cross-platform invocation for the legacy resolve shell entrypoint."""

    if os.name == "nt":
        return ["bash", str(LEGACY_ENTRYPOINT)]
    return [str(LEGACY_ENTRYPOINT)]


def _run_command(
    command: list[str],
    fake_python: Path,
    tmp_path: Path,
    stdin_text: str = "",
    *,
    cwd: Path = REPO_ROOT,
    include_fake_arthexis: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a resolve entrypoint with a deterministic fake Python interpreter."""
    env = dict(os.environ)
    env["PYTHON"] = str(fake_python)

    path_prefix = str(tmp_path)
    if include_fake_arthexis:
        fake_arthexis = tmp_path / "arthexis"
        fake_arthexis.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            f"exec {sys.executable} -m arthexis \"$@\"\n",
            encoding="utf-8",
        )
        fake_arthexis.chmod(fake_arthexis.stat().st_mode | stat.S_IEXEC)

    env["PATH"] = os.pathsep.join(filter(None, [path_prefix, env.get("PATH")]))
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        input=stdin_text,
        text=True,
        capture_output=True,
        check=False,
    )




