"""Tests for runserver migration preflight policy defaults and validation."""

from __future__ import annotations

import subprocess
from pathlib import Path


def _run_shell(script: str) -> subprocess.CompletedProcess[str]:
    """Run a bash snippet from the repository root and return the completed process."""

    return subprocess.run(
        ["bash", "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )


def test_default_migration_policy_prefers_check_for_satellite() -> None:
    """Satellite nodes should default preflight policy to check-only mode."""

    result = _run_shell(
        """
        set -e
        source scripts/helpers/common.sh
        source scripts/helpers/runserver_preflight.sh
        NODE_ROLE=Satellite
        default_migration_policy
        """
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "check"


def test_default_migration_policy_prefers_apply_for_terminal() -> None:
    """Terminal nodes should default preflight policy to apply mode."""

    result = _run_shell(
        """
        set -e
        source scripts/helpers/common.sh
        source scripts/helpers/runserver_preflight.sh
        NODE_ROLE=Terminal
        default_migration_policy
        """
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "apply"


def test_resolve_migration_policy_rejects_unknown_value() -> None:
    """Invalid migration policy values should fail with a clear error message."""

    result = _run_shell(
        """
        source scripts/helpers/common.sh
        source scripts/helpers/runserver_preflight.sh
        ARTHEXIS_MIGRATION_POLICY=invalid
        resolve_migration_policy
        """
    )

    assert result.returncode != 0
    assert "Unsupported ARTHEXIS_MIGRATION_POLICY" in result.stderr
