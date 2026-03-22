"""Tests for the legacy ``apps.ocpp.evcs`` compatibility module."""

from apps.ocpp import evcs as legacy_evcs
from apps.simulators import evcs as simulator_evcs


def test_legacy_evcs_module_re_exports_simulator_helpers() -> None:
    """Legacy imports should resolve to the simulator helper implementations."""

    assert legacy_evcs.parse_repeat is simulator_evcs.parse_repeat
    assert legacy_evcs.simulate is simulator_evcs.simulate
    assert legacy_evcs.simulate_cp is simulator_evcs.simulate_cp
    assert legacy_evcs._start_simulator is simulator_evcs._start_simulator
    assert legacy_evcs._stop_simulator is simulator_evcs._stop_simulator
    assert legacy_evcs.get_simulator_state is simulator_evcs.get_simulator_state
    assert legacy_evcs.view_simulator is simulator_evcs.view_simulator
    assert legacy_evcs.view_cp_simulator is simulator_evcs.view_cp_simulator
    assert legacy_evcs._simulator_status_json is simulator_evcs._simulator_status_json
