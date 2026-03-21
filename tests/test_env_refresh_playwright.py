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
        f"""
        source ./env-refresh.sh
        uname() {{ echo Linux; }}
        id() {{ if [ "$1" = "-u" ]; then echo 1000; else command id "$@"; fi; }}
        sudo() {{ return 1; }}
        PYTHON="{tmp_path}/fake-python"
        ensure_playwright_host_dependencies
        """,
        tmp_path=tmp_path,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "Linux host libraries may still be missing" in output
    assert f"'{tmp_path}/fake-python -m playwright install-deps chromium firefox'" in output


def test_playwright_browser_install_reports_missing_host_dependencies(tmp_path: Path):
    fake_python = tmp_path / "fake-python.sh"
    command_log = tmp_path / "python-commands.log"
    fake_python.write_text(
        "#!/usr/bin/env bash\n"
        f"echo \"$*\" >> \"{command_log}\"\n"
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
    assert not (lock_dir / "playwright.version").exists()
    assert "-m playwright install chromium firefox" in command_log.read_text(encoding="utf-8")


def test_playwright_browser_verify_runs_when_marker_already_exists(tmp_path: Path):
    fake_python = tmp_path / "fake-python.sh"
    command_log = tmp_path / "python-commands.log"
    fake_python.write_text(
        "#!/usr/bin/env bash\n"
        f"echo \"$*\" >> \"{command_log}\"\n"
        "if [ \"$1\" = \"-m\" ]; then\n"
        "  echo 'browser install should be skipped' >&2\n"
        "  exit 99\n"
        "fi\n"
        "echo 'Host system is missing dependencies for Chromium' >&2\n"
        "exit 10\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    marker = lock_dir / "playwright.version"
    marker.write_text("1.2.3\n", encoding="utf-8")

    result = _run_bash(
        f"""
        source ./env-refresh.sh
        PYTHON="{fake_python}"
        LOCK_DIR="{lock_dir}"
        FORCE_REFRESH=0
        ensure_playwright_installed() {{ return 0; }}
        playwright_version() {{ echo 1.2.3; }}
        ensure_playwright_host_dependencies() {{ return 0; }}
        ensure_playwright_browsers_installed
        """,
        tmp_path=tmp_path,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "playwright browsers already installed for version 1.2.3; skipping" in output
    assert "still missing host libraries for browser execution" in output
    assert "-m playwright install chromium firefox" not in command_log.read_text(encoding="utf-8")
    assert marker.read_text(encoding="utf-8").strip() == "1.2.3"


def test_playwright_host_dependency_install_failure_warns_and_continues(tmp_path: Path):
    fake_python = tmp_path / "fake-python.sh"
    command_log = tmp_path / "python-commands.log"
    fake_python.write_text(
        "#!/usr/bin/env bash\n"
        f"echo \"$*\" >> \"{command_log}\"\n"
        "if [ \"$1\" = \"-m\" ] && [ \"$2\" = \"playwright\" ] && [ \"$3\" = \"install\" ]; then\n"
        "  exit 0\n"
        "fi\n"
        "if [ \"$1\" = \"-m\" ] && [ \"$2\" = \"playwright\" ] && [ \"$3\" = \"install-deps\" ]; then\n"
        "  echo 'install-deps failed' >&2\n"
        "  exit 1\n"
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
        uname() {{ echo Linux; }}
        id() {{ if [ "$1" = "-u" ]; then echo 0; else command id "$@"; fi; }}
        ensure_playwright_installed() {{ return 0; }}
        playwright_version() {{ echo 1.2.3; }}
        ensure_playwright_browsers_installed
        """,
        tmp_path=tmp_path,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "Warning: Host dependency installation failed, but continuing to verification." in output
    assert "still missing host libraries for browser execution" in output
    assert not (lock_dir / "playwright.version").exists()

    commands = command_log.read_text(encoding="utf-8")
    assert "-m playwright install chromium firefox" in commands
    assert "-m playwright install-deps chromium firefox" in commands
