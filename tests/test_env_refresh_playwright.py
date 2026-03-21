"""Regression coverage for Playwright dependency handling in ``env-refresh.sh``."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _run_bash(script: str, *, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    """Run *script* in bash from the repository root and capture combined output."""

    return subprocess.run(
        ["bash", "-lc", script],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "ARTHEXIS_ENV_REFRESH_SOURCE_ONLY": "1",
            "ARTHEXIS_RUN_AS_USER": "1",
            "TMPDIR": str(tmp_path),
        },
        check=False,
    )


def test_playwright_host_dependency_install_warns_without_root_access(tmp_path: Path):
    result = _run_bash(
        """
        source ./env-refresh.sh
        uname() { echo Linux; }
        id() { if [ "$1" = "-u" ]; then echo 1000; else command id "$@"; fi; }
        sudo() { return 1; }
        PYTHON=/tmp/fake-python
        ensure_playwright_host_dependencies
        """,
        tmp_path=tmp_path,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "Linux host libraries may still be missing" in output
    assert "playwright install-deps chromium firefox" in output


def test_playwright_browser_install_reports_missing_host_dependencies(tmp_path: Path):
    fake_python = tmp_path / "fake-python.sh"
    fake_python.write_text(
        "#!/usr/bin/env bash\n"
        "if [ \"$1\" = \"-m\" ]; then\n"
        "  exit 0\n"
        "fi\n"
        "echo 'Host system is missing dependencies for Chromium' >&2\n"
        "exit 10\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()

    result = _run_bash(
        f"""
        source ./env-refresh.sh
        PYTHON="{fake_python}"
        LOCK_DIR="{lock_dir}"
        FORCE_REFRESH=1
        ensure_playwright_installed() {{ return 0; }}
        playwright_version() {{ echo 1.2.3; }}
        ensure_playwright_host_dependencies() {{ return 0; }}
        ensure_playwright_browsers_installed
        """,
        tmp_path=tmp_path,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "still missing host libraries for browser execution" in output
    assert (lock_dir / "playwright.version").read_text(encoding="utf-8").strip() == "1.2.3"
