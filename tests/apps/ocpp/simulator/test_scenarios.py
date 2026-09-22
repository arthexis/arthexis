from asgiref.sync import async_to_sync
from django.test import TestCase

from apps.ocpp.protocol.contracts import Direction, ProtocolVersion
from apps.ocpp.protocol.registry import ALL_ACTIONS
from apps.ocpp.simulator import run_v16_scenario, run_v201_scenario
from tests.apps.ocpp.builders import charger


class OcppSimulatorTests(TestCase):
    def test_v16_protocol_client_exercises_every_retained_inbound_action(self) -> None:
        completed = async_to_sync(run_v16_scenario)(charger("simulator-v16"))

        self.assertEqual(set(completed), self._inbound_actions(ProtocolVersion.OCPP_16))
        self.assertEqual(len(completed), len(set(completed)))

    def test_v201_protocol_client_exercises_every_retained_inbound_action(self) -> None:
        completed = async_to_sync(run_v201_scenario)(charger("simulator-v201"))

        self.assertEqual(
            set(completed), self._inbound_actions(ProtocolVersion.OCPP_201)
        )
        self.assertEqual(len(completed), len(set(completed)))

    @staticmethod
    def _inbound_actions(version: ProtocolVersion) -> set[str]:
        return {
            contract.action
            for contract in ALL_ACTIONS
            if contract.version == version
            and contract.direction == Direction.CHARGE_POINT_TO_CSMS
        }
