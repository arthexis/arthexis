"""Tests for the Mobility House EVCS simulator proposal module."""

from apps.simulators.evcs_mobilityhouse import (
    MobilityHouseOcppUnavailableError,
    MobilityHouseSimulatorConfig,
    build_simulator_proposal,
    ensure_mobilityhouse_ocpp_available,
)


def test_proposal_requires_ocpp_dependency():
    """The proposal should fail fast when the optional dependency is missing."""

    try:
        ensure_mobilityhouse_ocpp_available()
    except MobilityHouseOcppUnavailableError:
        return
    assert False, "Expected MobilityHouseOcppUnavailableError when ocpp is not installed."


def test_build_simulator_proposal_propagates_missing_dependency():
    """Building the proposal should expose the same dependency error."""

    config = MobilityHouseSimulatorConfig(
        charge_point_id="CP-PROPOSAL-1",
        central_system_uri="ws://localhost:8000/ws/ocpp/CP-PROPOSAL-1",
    )

    try:
        build_simulator_proposal(config)
    except MobilityHouseOcppUnavailableError:
        return
    assert False, "Expected MobilityHouseOcppUnavailableError when ocpp is not installed."
