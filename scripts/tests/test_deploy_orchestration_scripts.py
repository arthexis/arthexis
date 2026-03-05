"""Regression checks for detached deploy orchestration shell scripts."""

from pathlib import Path


ORCHESTRATOR_PATH = Path(__file__).resolve().parents[2] / "scripts/helpers/predeploy-migrate-orchestrator.sh"


def test_orchestrator_runs_migrations_before_service_switch() -> None:
    """The orchestrator should run migrate/apply checks before service stop/start operations."""
    contents = ORCHESTRATOR_PATH.read_text(encoding="utf-8")
    assert '"$python_bin" manage.py apply_release_migrations "$target_version"' in contents
    assert '"$python_bin" manage.py migrate --noinput' in contents
    assert '"$python_bin" manage.py migrate --check' in contents
    assert 'control_service stop "$SERVICE_NAME"' in contents
    assert contents.index('"$python_bin" manage.py migrate --check') < contents.index(
        'control_service stop "$SERVICE_NAME"'
    )
