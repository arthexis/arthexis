import pytest
from asgiref.sync import async_to_sync

from apps.ocpp.protocol.contracts import Direction, ProtocolVersion
from apps.ocpp.protocol.registry import ALL_ACTIONS
from apps.ocpp.simulator import (
    run_v16_authorization_scenario,
    run_v16_scenario,
    run_v201_scenario,
)
from tests.apps.ocpp.builders import charger

pytestmark = pytest.mark.django_db


def inbound_actions(version: ProtocolVersion) -> set[str]:
    return {
        contract.action
        for contract in ALL_ACTIONS
        if contract.version == version
        and contract.direction == Direction.CHARGE_POINT_TO_CSMS
    }


def test_v16_authorization_scenario_preserves_order_repeats_and_actual_status() -> None:
    target = charger(
        "authorization-matrix",
        authorization_mode="open",
    )

    results = async_to_sync(run_v16_authorization_scenario)(
        target,
        ("known-shape", "unknown-shape", "known-shape"),
    )

    assert [(result.id_tag, result.status) for result in results] == [
        ("known-shape", "Accepted"),
        ("unknown-shape", "Accepted"),
        ("known-shape", "Accepted"),
    ]


def test_v16_protocol_client_exercises_every_retained_inbound_action() -> None:
    completed = async_to_sync(run_v16_scenario)(charger("simulator-v16"))

    assert set(completed) == inbound_actions(ProtocolVersion.OCPP_16)
    assert len(completed) == len(set(completed))


def test_v201_protocol_client_exercises_every_retained_inbound_action() -> None:
    completed = async_to_sync(run_v201_scenario)(charger("simulator-v201"))

    assert set(completed) == inbound_actions(ProtocolVersion.OCPP_201)
    assert len(completed) == len(set(completed))
