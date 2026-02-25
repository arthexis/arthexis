"""Regression checks for detached deploy orchestration shell scripts."""

from pathlib import Path

import pytest


pytestmark = pytest.mark.regression


WATCH_UPGRADE_PATH = Path(__file__).resolve().parents[2] / "scripts/helpers/watch-upgrade.sh"
ORCHESTRATOR_PATH = Path(__file__).resolve().parents[2] / "scripts/helpers/predeploy-migrate-orchestrator.sh"


def test_watch_upgrade_uses_predeploy_orchestrator() -> None:
    """watch-upgrade should delegate migration-first deployments to the orchestrator helper."""
    contents = WATCH_UPGRADE_PATH.read_text(encoding="utf-8")
    assert 'ORCHESTRATOR="$BASE_DIR/scripts/helpers/predeploy-migrate-orchestrator.sh"' in contents
    assert '(cd "$BASE_DIR" && "$ORCHESTRATOR" "$SERVICE_NAME" "${UPGRADE_CMD[@]}") || STATUS=$?' in contents


def test_orchestrator_runs_migrations_before_service_switch() -> None:
    """The orchestrator should run migrate/apply checks before service stop/start operations."""
    contents = ORCHESTRATOR_PATH.read_text(encoding="utf-8")
    assert '"$python_bin" manage.py migrate --noinput' in contents
    assert '"$python_bin" manage.py migrate --check' in contents
    assert 'control_service stop "$SERVICE_NAME"' in contents
    assert contents.index('"$python_bin" manage.py migrate --check') < contents.index('control_service stop "$SERVICE_NAME"')


def test_orchestrator_emits_structured_timestamps_and_marker() -> None:
    """The orchestrator should emit structured timing logs and persist a migration marker."""
    contents = ORCHESTRATOR_PATH.read_text(encoding="utf-8")
    assert 'log_event "predeploy_migrate" "start"' in contents
    assert 'log_event "deploy_orchestration" "start"' in contents
    assert '"elapsed_seconds":%s' in contents
    assert 'MIGRATION_MARKER_FILE="${LOCK_DIR}/predeploy_migrate_success.json"' in contents
    assert '"status": "success"' in contents
