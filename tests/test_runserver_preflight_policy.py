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


def test_default_migration_policy_reads_role_file_when_env_missing(tmp_path: Path) -> None:
    """Policy defaults should fall back to role.lck when NODE_ROLE is unset."""

    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    (lock_dir / "role.lck").write_text("Satellite\n", encoding="utf-8")

    result = _run_shell(
        f"""
        set -e
        source scripts/helpers/common.sh
        export LOCK_DIR={str(lock_dir)!r}
        source scripts/helpers/runserver_preflight.sh
        unset NODE_ROLE
        default_migration_policy
        """
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "check"


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


def test_runserver_preflight_skips_when_policy_skip() -> None:
    """run_runserver_preflight should short-circuit when policy is skip."""

    result = _run_shell(
        """
        source scripts/helpers/common.sh
        source scripts/helpers/runserver_preflight.sh
        ARTHEXIS_MIGRATION_POLICY=skip
        run_runserver_preflight
        """
    )

    assert result.returncode == 0
    assert "Skipping runserver migration preflight" in result.stdout


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
