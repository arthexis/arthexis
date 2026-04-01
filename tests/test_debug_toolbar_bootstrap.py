"""Tests for Debug Toolbar bootstrap behavior in start scripts."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        sys.platform == "win32",
        reason="bash helper scripts are not supported on Windows",
    ),
]


def _run_shell(script: str) -> subprocess.CompletedProcess[str]:
    """Run a bash snippet from the repository root and return the completed process."""

    return subprocess.run(
        ["bash", "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )


def test_debug_toolbar_bootstrap_installs_when_module_missing() -> None:
    """Ensure missing debug_toolbar triggers pip installation."""

    result = _run_shell(
        """
        set -e
        source scripts/helpers/debug_toolbar.sh

        python() {
            if [ "$1" = "-m" ] && [ "$2" = "pip" ] && [ "$3" = "install" ]; then
                echo "pip:$4"
                return 0
            fi
            return 1
        }

        export ARTHEXIS_DEBUG_TOOLBAR_REQUIREMENT='django-debug-toolbar==6.2.0'
        arthexis_ensure_debug_toolbar_installed python
        """
    )

    assert result.returncode == 0
    assert "pip:django-debug-toolbar==6.2.0" in result.stdout


def test_debug_toolbar_bootstrap_skips_install_when_module_exists() -> None:
    """Ensure existing debug_toolbar does not trigger pip installation."""

    result = _run_shell(
        """
        set -e
        source scripts/helpers/debug_toolbar.sh

        python() {
            if [ "$1" = "-" ]; then
                return 0
            fi
            if [ "$1" = "-m" ] && [ "$2" = "pip" ] && [ "$3" = "install" ]; then
                echo "pip-called"
                return 0
            fi
            return 0
        }

        arthexis_ensure_debug_toolbar_installed python
        """
    )

    assert result.returncode == 0
    assert "pip-called" not in result.stdout
