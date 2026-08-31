from __future__ import annotations

from pathlib import Path

from gate_markers import gate


pytestmark = [gate.upgrade]

ROOT = Path(__file__).resolve().parents[3]


def _script(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_delegated_upgrade_passes_virtualenv_to_transient_unit() -> None:
    script = _script("scripts/delegated-upgrade.sh")

    assert 'PYTHON_BIN="$VENV_BIN/python"' in script
    assert '--setenv "ARTHEXIS_PYTHON_BIN=$PYTHON_BIN"' in script
    assert '--setenv "VIRTUAL_ENV=$VENV_DIR"' in script
    assert '--setenv "PATH=$VENV_BIN:${PATH:-' in script
    assert script.index('--setenv "ARTHEXIS_PYTHON_BIN=$PYTHON_BIN"') < script.index(
        'DELEGATED_CMD+=("$WATCH_HELPER")'
    )


def test_watch_upgrade_exports_virtualenv_for_direct_invocations() -> None:
    script = _script("scripts/helpers/watch-upgrade.sh")

    assert 'export ARTHEXIS_PYTHON_BIN="$VENV_BIN/python"' in script
    assert 'export VIRTUAL_ENV="${VIRTUAL_ENV:-$VENV_DIR}"' in script
    assert 'export PATH="$VENV_BIN:${PATH:-' in script


def test_predeploy_orchestrator_stops_service_stack_before_migrations() -> None:
    script = _script("scripts/helpers/predeploy-migrate-orchestrator.sh")

    assert '. "$BASE_DIR/scripts/helpers/service_manager.sh"' in script
    assert "control_service_stack stop" in script
    assert "trap cleanup_service_stack EXIT" in script
    assert "control_service_stack start" in script
    main_start = script.index('log_event "deploy_orchestration" "start"')
    stop_index = script.index("control_service_stack stop", main_start)
    migrate_index = script.index("run_predeploy_migrations", stop_index)
    deploy_index = script.index(
        'if [ -x "${DEPLOY_CMD[0]}" ]; then'
    )
    start_index = script.index("control_service_stack start", deploy_index)

    assert stop_index < migrate_index < deploy_index < start_index
