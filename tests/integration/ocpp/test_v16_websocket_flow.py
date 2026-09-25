from decimal import Decimal

from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator
from django.contrib.auth.hashers import make_password
from django.test import TestCase

from apps.cards.models import CardCredential
from apps.energy.models import CustomerAccount
from apps.ocpp.domain.snapshots import snapshot_charger
from apps.ocpp.models import (
    Charger,
    Connector,
    NotificationRecord,
    OcppTransaction,
    OperationalStatusRecord,
)
from arthexis.asgi import application
from tests.apps.ocpp.builders import charger
from tests.integration.ocpp.support import basic_authorization


class Ocpp16WebsocketFlowTests(TestCase):
    def setUp(self) -> None:
        account = CustomerAccount.objects.create(
            key="account-1",
            name="Account",
            ocpp_id_tag="account-tag",
        )
        CardCredential.objects.create(
            external_id="card-1",
            account=account,
            ocpp_id_tag="card-tag",
        )
        charger(
            "charger-1",
            connection_token_hash=make_password("charger-secret"),
        )

    def test_authorize_and_transaction_flow(self) -> None:
        async_to_sync(self._run_exchange)()

        transaction = OcppTransaction.objects.get()
        self.assertEqual(transaction.meter_start, 100)
        self.assertEqual(transaction.meter_stop, 150)
        self.assertEqual(transaction.energy_kwh, Decimal("0.0500"))
        self.assertEqual(transaction.meter_values.count(), 1)
        self.assertEqual(Connector.objects.get().status, "Preparing")
        self.assertEqual(NotificationRecord.objects.get().action, "DataTransfer")
        self.assertEqual(OperationalStatusRecord.objects.count(), 2)


    def test_restart_reconnect_reconciles_open_transaction_from_fresh_status(self) -> None:
        async_to_sync(self._run_reconnect_recovery_exchange)()

        selected = Charger.objects.get(identity="charger-1")
        transaction = OcppTransaction.objects.get()
        snapshot = snapshot_charger(selected)

        self.assertIsNone(transaction.stopped_at)
        self.assertEqual(
            transaction.recovery_state,
            OcppTransaction.RecoveryState.UNRESOLVED,
        )
        self.assertEqual(snapshot.state, "offline")
        self.assertEqual(snapshot.active_transactions, 0)
        self.assertEqual(snapshot.unresolved_sessions, 1)
        self.assertIsNone(snapshot.current_transaction_id)

    async def _run_reconnect_recovery_exchange(self) -> None:
        first = WebsocketCommunicator(
            application,
            "/ws/ocpp/charger-1/",
            subprotocols=["ocpp1.6"],
            headers=[(b"authorization", basic_authorization())],
        )
        connected, subprotocol = await first.connect(timeout=5)
        self.assertTrue(connected)
        self.assertEqual(subprotocol, "ocpp1.6")

        await first.send_json_to(
            [
                2,
                "boot-recovery-1",
                "BootNotification",
                {"chargePointVendor": "ACME", "chargePointModel": "Test"},
            ]
        )
        self.assertEqual((await first.receive_json_from())[2]["status"], "Accepted")

        await first.send_json_to(
            [2, "authorize-recovery-1", "Authorize", {"idTag": "card-tag"}]
        )
        self.assertEqual(
            (await first.receive_json_from())[2]["idTagInfo"]["status"],
            "Accepted",
        )

        await first.send_json_to(
            [
                2,
                "start-recovery-1",
                "StartTransaction",
                {"connectorId": 1, "idTag": "card-tag", "meterStart": 100},
            ]
        )
        started = await first.receive_json_from()
        self.assertEqual(started[2]["idTagInfo"]["status"], "Accepted")

        await first.disconnect()

        second = WebsocketCommunicator(
            application,
            "/ws/ocpp/charger-1/",
            subprotocols=["ocpp1.6"],
            headers=[(b"authorization", basic_authorization())],
        )
        connected, subprotocol = await second.connect(timeout=5)
        self.assertTrue(connected)
        self.assertEqual(subprotocol, "ocpp1.6")

        await second.send_json_to(
            [
                2,
                "boot-recovery-2",
                "BootNotification",
                {"chargePointVendor": "ACME", "chargePointModel": "Test"},
            ]
        )
        self.assertEqual((await second.receive_json_from())[2]["status"], "Accepted")

        await second.send_json_to(
            [
                2,
                "status-recovery-available",
                "StatusNotification",
                {
                    "connectorId": 1,
                    "status": "Available",
                    "timestamp": "2099-01-01T00:00:00Z",
                },
            ]
        )
        self.assertEqual(
            await second.receive_json_from(),
            [3, "status-recovery-available", {}],
        )

        await second.disconnect()

    async def _run_exchange(self) -> None:
        communicator = WebsocketCommunicator(
            application,
            "/ws/ocpp/charger-1/",
            subprotocols=["ocpp1.6"],
            headers=[(b"authorization", basic_authorization())],
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
        self.assertIn("currentTime", (await communicator.receive_json_from())[2])

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
        transaction_id = response[2]["transactionId"]
        self.assertEqual(response[2]["idTagInfo"]["status"], "Accepted")

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

        for unique_id, action, payload, expected in (
            ("transfer-1", "DataTransfer", {"vendorId": "ACME"}, {"status": "Accepted"}),
            ("diagnostics-1", "DiagnosticsStatusNotification", {"status": "Uploaded"}, {}),
            ("firmware-1", "FirmwareStatusNotification", {"status": "Downloaded"}, {}),
        ):
            await communicator.send_json_to([2, unique_id, action, payload])
            self.assertEqual(
                await communicator.receive_json_from(),
                [3, unique_id, expected],
            )

        await communicator.disconnect()
