"""Tests for the Mobility House EVCS simulator proposal module."""

import pytest

from apps.simulators.evcs_mobilityhouse import (
    MobilityHouseChargePointAdapter,
    MobilityHouseOcppUnavailableError,
    MobilityHouseSimulatorConfig,
    build_simulator_proposal,
    ensure_mobilityhouse_ocpp_available,
)


def test_proposal_requires_ocpp_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    """The proposal should fail fast when the optional dependency is missing."""

    monkeypatch.setattr("apps.simulators.evcs_mobilityhouse.find_spec", lambda _: None)

    with pytest.raises(MobilityHouseOcppUnavailableError):
        ensure_mobilityhouse_ocpp_available()


def test_build_simulator_proposal_propagates_missing_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Building the proposal should expose the same dependency error."""

    monkeypatch.setattr("apps.simulators.evcs_mobilityhouse.find_spec", lambda _: None)

    config = MobilityHouseSimulatorConfig(
        charge_point_id="CP-PROPOSAL-1",
        central_system_uri="ws://localhost:8000/ws/ocpp/CP-PROPOSAL-1",
    )

    with pytest.raises(MobilityHouseOcppUnavailableError):
        build_simulator_proposal(config)


def test_build_simulator_proposal_succeeds_when_dependency_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Proposal construction should succeed when the optional dependency is available."""

    monkeypatch.setattr("apps.simulators.evcs_mobilityhouse.find_spec", lambda _: object())

    config = MobilityHouseSimulatorConfig(
        charge_point_id="CP-PROPOSAL-1",
        central_system_uri="ws://localhost:8000/ws/ocpp/CP-PROPOSAL-1",
    )

    proposal = build_simulator_proposal(config)

    assert proposal.config == config
    assert (
        proposal.adapter_path
        == "apps.simulators.evcs_mobilityhouse.MobilityHouseChargePointAdapter"
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("charge_point_id", "message_id", "response_frame", "expected_payload"),
    [
        (
            "CP-READ-1",
            "msg-1",
            [3, "msg-1", {"status": "Accepted"}],
            {"status": "Accepted"},
        ),
        (
            "CP-READ-ERR-1",
            "msg-err-1",
            [4, "msg-err-1", "InternalError", "failure", {}],
            ["InternalError", "failure", {}],
        ),
    ],
)
async def test_read_response_handles_response_without_prior_call(
    monkeypatch: pytest.MonkeyPatch,
    charge_point_id: str,
    message_id: str,
    response_frame: list[object],
    expected_payload: object,
) -> None:
    """Receiving a direct response frame should not crash when no Call was observed."""

    monkeypatch.setattr("apps.simulators.evcs_mobilityhouse.find_spec", lambda _: object())

    config = MobilityHouseSimulatorConfig(
        charge_point_id=charge_point_id,
        central_system_uri=f"ws://localhost:8000/ws/ocpp/{charge_point_id}",
    )
    adapter = MobilityHouseChargePointAdapter(config)

    async def recv_stub(_ws, *, timeout: float = 60.0):
        assert timeout > 0
        return response_frame

    adapter._recv = recv_stub

    payload, call = await adapter._read_response(object(), expected_message_id=message_id)

    assert payload == expected_payload
    assert call is None
