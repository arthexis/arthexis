from asgiref.sync import async_to_sync
import pytest
from channels.testing import WebsocketCommunicator
from django.contrib.auth.hashers import make_password
from django.test import override_settings

from apps.ocpp.models import Charger
from arthexis.asgi import application
from tests.integration.ocpp.support import basic_authorization


pytestmark = pytest.mark.django_db(transaction=True)

class OcppEnrollmentFlowTests:
    def test_websocket_enrolls_new_charger(self) -> None:
        with override_settings(OCPP_ENROLLMENT_TOKEN_HASH=make_password("enroll")):
            async_to_sync(self._enroll)()

        selected = Charger.objects.get(identity="charger-new")
        assert selected.enrolled_at is not None
        assert selected.authorization_mode == Charger.AuthorizationMode.OPEN

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
        assert connected
        assert subprotocol == "ocpp1.6"

        await communicator.send_json_to(
            [
                2,
                "boot-new",
                "BootNotification",
                {"chargePointVendor": "ACME", "chargePointModel": "New"},
            ]
        )
        response = await communicator.receive_json_from()
        assert response[:2] == [3, "boot-new"]
        assert response[2]["status"] == "Accepted"
        assert response[2]["interval"] == 300
        assert "currentTime" in response[2]
        await communicator.disconnect()
