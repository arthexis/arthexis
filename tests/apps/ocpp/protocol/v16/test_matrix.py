from django.test import TestCase

from apps.ocpp.protocol.contracts import Direction, ProtocolVersion
from apps.ocpp.protocol.registry import ALL_ACTIONS
from apps.ocpp.protocol.v16.inbound import InboundActions
from apps.ocpp.protocol.v16.outbound import VALIDATORS
from tests.apps.ocpp.builders import charger


class Ocpp16MatrixTests(TestCase):
    def test_every_frozen_v16_action_has_a_handler_or_outbound_validator(self) -> None:
        selected = charger("charger-1")
        inbound_actions = set(InboundActions(selected)._handlers)
        expected_inbound = {
            contract.action
            for contract in ALL_ACTIONS
            if contract.version == ProtocolVersion.OCPP_16
            and contract.direction == Direction.CHARGE_POINT_TO_CSMS
        }
        expected_outbound = {
            contract.action
            for contract in ALL_ACTIONS
            if contract.version == ProtocolVersion.OCPP_16
            and contract.direction == Direction.CSMS_TO_CHARGE_POINT
        }

        self.assertEqual(inbound_actions, expected_inbound)
        self.assertEqual(set(VALIDATORS), expected_outbound)
