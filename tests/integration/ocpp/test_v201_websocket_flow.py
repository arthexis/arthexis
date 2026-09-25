from asgiref.sync import async_to_sync
from django.contrib.auth.hashers import make_password
from django.test import TestCase

from tests.apps.ocpp.builders import charger
from tests.integration.ocpp.support import connect_charger


class Ocpp201WebsocketFlowTests(TestCase):
    def setUp(self) -> None:
        charger(
            "charger-1",
            connection_token_hash=make_password("charger-secret"),
        )

    def test_negotiates_and_dispatches_ocpp_201(self) -> None:
        async_to_sync(self._connect)()

    async def _connect(self) -> None:
        communicator = await connect_charger(subprotocol="ocpp2.0.1")

        await communicator.send_json_to([2, "heartbeat-1", "Heartbeat", {}])
        response = await communicator.receive_json_from()
        self.assertIn("currentTime", response[2])

        await communicator.send_json_to(
            [
                2,
                "boot-201",
                "BootNotification",
                {"chargingStation": {"model": "Test", "vendorName": "ACME"}},
            ]
        )
        response = await communicator.receive_json_from()
        self.assertEqual(response[2]["status"], "Accepted")

        await communicator.send_json_to(
            [2, "invalid-boot-201", "BootNotification", {}]
        )
        response = await communicator.receive_json_from()
        self.assertEqual(response[2], "FormationViolation")
        await communicator.disconnect()
