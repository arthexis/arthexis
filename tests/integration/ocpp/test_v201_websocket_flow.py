from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator
from django.contrib.auth.hashers import make_password
from django.test import TestCase, tag

from arthexis.asgi import application
from tests.apps.ocpp.builders import charger
from tests.integration.ocpp.support import basic_authorization


@tag("main")
class Ocpp201WebsocketFlowTests(TestCase):
    def setUp(self) -> None:
        charger(
            "charger-1",
            connection_token_hash=make_password("charger-secret"),
        )

    def test_negotiates_and_dispatches_ocpp_201(self) -> None:
        async_to_sync(self._connect)()

    async def _connect(self) -> None:
        communicator = WebsocketCommunicator(
            application,
            "/ws/ocpp/charger-1/",
            subprotocols=["ocpp2.0.1"],
            headers=[(b"authorization", basic_authorization())],
        )
        connected, subprotocol = await communicator.connect(timeout=5)
        self.assertTrue(connected)
        self.assertEqual(subprotocol, "ocpp2.0.1")

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
