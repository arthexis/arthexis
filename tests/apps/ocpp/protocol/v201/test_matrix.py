import pytest

from apps.ocpp.protocol.contracts import Direction, ProtocolVersion
from apps.ocpp.protocol.registry import ALL_ACTIONS
from apps.ocpp.protocol.v201.inbound import InboundActions
from apps.ocpp.protocol.v201.outbound import VALIDATORS
from tests.apps.ocpp.builders import charger

pytestmark = pytest.mark.django_db


def test_every_frozen_action_has_a_handler_or_outbound_validator() -> None:
    selected = charger("charger-201")
    expected_inbound = {
        contract.action
        for contract in ALL_ACTIONS
        if contract.version == ProtocolVersion.OCPP_201
        and contract.direction == Direction.CHARGE_POINT_TO_CSMS
    }
    expected_outbound = {
        contract.action
        for contract in ALL_ACTIONS
        if contract.version == ProtocolVersion.OCPP_201
        and contract.direction == Direction.CSMS_TO_CHARGE_POINT
    }

    assert set(InboundActions(selected)._handlers) == expected_inbound
    assert set(VALIDATORS) == expected_outbound
