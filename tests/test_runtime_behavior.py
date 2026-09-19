import asyncio
import base64
from datetime import timedelta
from decimal import Decimal
from io import StringIO

from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator
from django.contrib.auth.hashers import make_password
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.cards.models import AuthorizationAttempt, CardCredential
from apps.energy.models import CustomerAccount
from apps.events.models import EventEnvelope
from apps.ocpp.models import (
    Charger,
    Connector,
    NotificationRecord,
    OcppTransaction,
    OperationalStatusRecord,
)
from apps.ocpp.services.authorization import authorize_id_tag
from apps.ocpp.tasks import refresh_stale_connections
from arthexis.asgi import application
from tests.ocpp.builders import charger


class RuntimeBehaviorTests(TestCase):
    def setUp(self) -> None:
        self.account = CustomerAccount.objects.create(
            key="account-1", name="Account", ocpp_id_tag="account-tag"
        )
        self.card = CardCredential.objects.create(
            external_id="card-1", account=self.account, ocpp_id_tag="card-tag"
        )
        self.charger = charger(
            "charger-1",
            connection_token_hash=make_password("charger-secret"),
        )

    def test_authorization_records_attempt_and_event(self) -> None:
        result = authorize_id_tag(charger=self.charger, id_tag=self.card.ocpp_id_tag)

        self.assertTrue(result.accepted)
        self.assertEqual(result.account, self.account)
        self.assertTrue(AuthorizationAttempt.objects.get().accepted)
        self.assertEqual(EventEnvelope.objects.get().event_type, "ocpp.authorization")

    def test_event_command_accepts_publish_alias(self) -> None:
        output = StringIO()

        call_command(
            "event",
            "pub",
            "test.event",
            "--producer",
            "tests",
            "--payload",
            '{"value": 1}',
            stdout=output,
        )

        self.assertEqual(EventEnvelope.objects.get().payload, {"value": 1})

    def test_stale_connection_task_only_changes_stale_chargers(self) -> None:
        Charger.objects.filter(pk=self.charger.pk).update(
            connected_at=timezone.now() - timedelta(hours=3)
        )

        self.assertEqual(refresh_stale_connections(), 1)
        self.charger.refresh_from_db()
        self.assertIsNone(self.charger.connected_at)

    def test_websocket_authorize_and_start_transaction(self) -> None:
        async_to_sync(self._run_ocpp_exchange)()

        transaction = OcppTransaction.objects.get()
        self.assertEqual(transaction.meter_start, 100)
        self.assertEqual(transaction.meter_stop, 150)
        self.assertEqual(transaction.energy_kwh, Decimal("0.0500"))
        self.assertEqual(transaction.meter_values.count(), 1)
        self.assertEqual(Connector.objects.get().status, "Preparing")
        self.assertEqual(NotificationRecord.objects.get().action, "DataTransfer")
        self.assertEqual(OperationalStatusRecord.objects.count(), 2)

    def test_websocket_negotiates_ocpp_201(self) -> None:
        async_to_sync(self._connect_ocpp_201)()

    def test_websocket_enrolls_a_new_charger_with_the_enrollment_credential(
        self,
    ) -> None:
        with override_settings(OCPP_ENROLLMENT_TOKEN_HASH=make_password("enroll")):
            async_to_sync(self._enroll_ocpp_charger)()

        charger = Charger.objects.get(identity="charger-new")
        self.assertIsNotNone(charger.enrolled_at)
        self.assertEqual(charger.authorization_mode, Charger.AuthorizationMode.OPEN)

    async def _run_ocpp_exchange(self) -> None:
        communicator = WebsocketCommunicator(
            application,
            "/ws/ocpp/charger-1/",
            subprotocols=["ocpp1.6"],
            headers=[(b"authorization", self._basic_authorization())],
        )
        connected, subprotocol = await communicator.connect(timeout=5)
        self.assertTrue(connected)
        self.assertEqual(subprotocol, "ocpp1.6")

        await communicator.send_json_to(
            [
                2,
                "boot-1",
                "BootNotification",
                {"chargePointVendor": "ACME", "chargePointModel": "Test"},
            ]
        )
        response = await communicator.receive_json_from()
        self.assertEqual(response[2]["status"], "Accepted")
        await communicator.send_json_to([2, "heartbeat-1", "Heartbeat", {}])
        response = await communicator.receive_json_from()
        self.assertIn("currentTime", response[2])
        await communicator.send_json_to(
            [
                2,
                "status-1",
                "StatusNotification",
                {"connectorId": 1, "status": "Preparing"},
            ]
        )
        self.assertEqual(await communicator.receive_json_from(), [3, "status-1", {}])

        await communicator.send_json_to(
            [2, "authorize-1", "Authorize", {"idTag": "card-tag"}]
        )
        response = await communicator.receive_json_from()
        self.assertEqual(response[2]["idTagInfo"]["status"], "Accepted")

        await communicator.send_json_to(
            [
                2,
                "start-1",
                "StartTransaction",
                {"connectorId": 1, "idTag": "card-tag", "meterStart": 100},
            ]
        )
        response = await communicator.receive_json_from()
        self.assertEqual(response[2]["idTagInfo"]["status"], "Accepted")
        transaction_id = response[2]["transactionId"]
        await communicator.send_json_to(
            [
                2,
                "meter-1",
                "MeterValues",
                {
                    "transactionId": transaction_id,
                    "meterValue": [
                        {
                            "timestamp": "2026-01-01T00:05:00Z",
                            "sampledValue": [{"value": "125"}],
                        }
                    ],
                },
            ]
        )
        self.assertEqual(await communicator.receive_json_from(), [3, "meter-1", {}])
        await communicator.send_json_to(
            [
                2,
                "stop-1",
                "StopTransaction",
                {"transactionId": transaction_id, "meterStop": 150},
            ]
        )
        self.assertEqual(
            await communicator.receive_json_from(),
            [3, "stop-1", {"idTagInfo": {"status": "Accepted"}}],
        )
        await communicator.send_json_to(
            [2, "transfer-1", "DataTransfer", {"vendorId": "ACME"}]
        )
        self.assertEqual(
            await communicator.receive_json_from(),
            [3, "transfer-1", {"status": "Accepted"}],
        )
        await communicator.send_json_to(
            [
                2,
                "diagnostics-1",
                "DiagnosticsStatusNotification",
                {"status": "Uploaded"},
            ]
        )
        self.assertEqual(
            await communicator.receive_json_from(), [3, "diagnostics-1", {}]
        )
        await communicator.send_json_to(
            [2, "firmware-1", "FirmwareStatusNotification", {"status": "Downloaded"}]
        )
        self.assertEqual(await communicator.receive_json_from(), [3, "firmware-1", {}])
        await communicator.disconnect()

    async def _connect_ocpp_201(self) -> None:
        communicator = WebsocketCommunicator(
            application,
            "/ws/ocpp/charger-1/",
            subprotocols=["ocpp2.0.1"],
            headers=[(b"authorization", self._basic_authorization())],
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
        await communicator.send_json_to([2, "invalid-boot-201", "BootNotification", {}])
        response = await communicator.receive_json_from()
        self.assertEqual(response[2], "FormationViolation")
        await communicator.disconnect()

    async def _enroll_ocpp_charger(self) -> None:
        communicator = WebsocketCommunicator(
            application,
            "/ws/ocpp/charger-new/",
            subprotocols=["ocpp1.6"],
            headers=[
                (b"authorization", self._basic_authorization("charger-new", "enroll"))
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

    @staticmethod
    def _basic_authorization(
        identity: str = "charger-1", token: str = "charger-secret"
    ) -> bytes:
        encoded = base64.b64encode(f"{identity}:{token}".encode())
        return b"Basic " + encoded
