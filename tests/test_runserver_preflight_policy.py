"""Integration test for runserver migration preflight policy enforcement."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.skipif(sys.platform == "win32", reason="bash helper scripts are not supported on Windows"),
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


def test_runserver_preflight_check_policy_fails_on_pending_migrations() -> None:
    """Check-only policy should fail fast when migrate --check reports pending work."""

    result = _run_shell(
        """
        set -e
        source scripts/helpers/common.sh
        source scripts/helpers/runserver_preflight.sh

        arthexis_python_bin() {
            echo mock_python
        }

        compute_migration_fingerprint() {
            echo fingerprint
        }

        mock_python() {
            if [ "$1" = "manage.py" ] && [ "$2" = "migrate" ] && [ "$3" = "--check" ]; then
                echo "pending migrations" >&2
                return 1
            fi
            return 0
        }

        ARTHEXIS_MIGRATION_POLICY=check
        run_runserver_preflight
        """
    )

    assert result.returncode != 0
    assert "policy is check-only" in result.stderr
