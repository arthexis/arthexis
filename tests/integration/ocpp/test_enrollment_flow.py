from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator
from django.contrib.auth.hashers import make_password
from django.test import TestCase, override_settings, tag

from apps.ocpp.models import Charger
from arthexis.asgi import application
from tests.integration.ocpp.support import basic_authorization


@tag("main")
class OcppEnrollmentFlowTests(TestCase):
    def test_websocket_enrolls_new_charger(self) -> None:
        with override_settings(OCPP_ENROLLMENT_TOKEN_HASH=make_password("enroll")):
            async_to_sync(self._enroll)()

        selected = Charger.objects.get(identity="charger-new")
        self.assertIsNotNone(selected.enrolled_at)
        self.assertEqual(selected.authorization_mode, Charger.AuthorizationMode.OPEN)

    async def _enroll(self) -> None:
        communicator = WebsocketCommunicator(
            application,
            "/ws/ocpp/charger-new/",
            subprotocols=["ocpp1.6"],
            headers=[
                (
                    b"authorization",
                    basic_authorization("charger-new", "enroll"),
                )
            ],
        )
        connected, subprotocol = await communicator.connect(timeout=15)
        self.assertTrue(connected)
        self.assertEqual(subprotocol, "ocpp1.6")

        await communicator.send_json_to(
            [
                2,
                "boot-new",
                "BootNotification",
                {"chargePointVendor": "ACME", "chargePointModel": "New"},
            ]
        )
        response = await communicator.receive_json_from()
        self.assertEqual(response[:2], [3, "boot-new"])
        self.assertEqual(response[2]["status"], "Accepted")
        self.assertEqual(response[2]["interval"], 300)
        self.assertIn("currentTime", response[2])
        await communicator.disconnect()
