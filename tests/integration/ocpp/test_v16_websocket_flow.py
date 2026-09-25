from decimal import Decimal

import pytest

from asgiref.sync import async_to_sync
from django.contrib.auth.hashers import make_password

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
from tests.apps.ocpp.builders import charger
from tests.integration.ocpp.support import connect_charger

pytestmark = pytest.mark.django_db(transaction=True)

class Ocpp16WebsocketFlowTests:
    def setup_method(self) -> None:
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
        assert transaction.meter_start == 100
        assert transaction.meter_stop == 150
        assert transaction.energy_kwh == Decimal("0.0500")
        assert transaction.meter_values.count() == 1
        assert Connector.objects.get().status == "Preparing"
        assert NotificationRecord.objects.get().action == "DataTransfer"
        assert OperationalStatusRecord.objects.count() == 2


    def test_restart_reconnect_reconciles_open_transaction_from_fresh_status(self) -> None:
        async_to_sync(self._run_reconnect_recovery_exchange)()

        selected = Charger.objects.get(identity="charger-1")
        transaction = OcppTransaction.objects.get()
        snapshot = snapshot_charger(selected)

        assert transaction.stopped_at is None
        assert transaction.recovery_state == OcppTransaction.RecoveryState.UNRESOLVED
        assert snapshot.state == "offline"
        assert snapshot.active_transactions == 0
        assert snapshot.unresolved_sessions == 1
        assert snapshot.current_transaction_id is None

    async def _run_reconnect_recovery_exchange(self) -> None:
        first = await connect_charger()

        await first.send_json_to(
            [
                2,
                "boot-recovery-1",
                "BootNotification",
                {"chargePointVendor": "ACME", "chargePointModel": "Test"},
            ]
        )
        assert (await first.receive_json_from())[2]["status"] == "Accepted"

        await first.send_json_to(
            [2, "authorize-recovery-1", "Authorize", {"idTag": "card-tag"}]
        )
        assert (await first.receive_json_from())[2]["idTagInfo"]["status"] == "Accepted"

        await first.send_json_to(
            [
                2,
                "start-recovery-1",
                "StartTransaction",
                {"connectorId": 1, "idTag": "card-tag", "meterStart": 100},
            ]
        )
        started = await first.receive_json_from()
        assert started[2]["idTagInfo"]["status"] == "Accepted"

        await first.disconnect()

        second = await connect_charger()

        await second.send_json_to(
            [
                2,
                "boot-recovery-2",
                "BootNotification",
                {"chargePointVendor": "ACME", "chargePointModel": "Test"},
            ]
        )
        assert (await second.receive_json_from())[2]["status"] == "Accepted"

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
        assert await second.receive_json_from() == [3, "status-recovery-available", {}]

        await second.disconnect()

    async def _run_exchange(self) -> None:
        communicator = await connect_charger()

        await communicator.send_json_to(
            [
                2,
                "boot-1",
                "BootNotification",
                {"chargePointVendor": "ACME", "chargePointModel": "Test"},
            ]
        )
        response = await communicator.receive_json_from()
        assert response[2]["status"] == "Accepted"

        await communicator.send_json_to([2, "heartbeat-1", "Heartbeat", {}])
        assert "currentTime" in (await communicator.receive_json_from())[2]

        await communicator.send_json_to(
            [
                2,
                "status-1",
                "StatusNotification",
                {"connectorId": 1, "status": "Preparing"},
            ]
        )
        assert await communicator.receive_json_from() == [3, "status-1", {}]

        await communicator.send_json_to(
            [2, "authorize-1", "Authorize", {"idTag": "card-tag"}]
        )
        response = await communicator.receive_json_from()
        assert response[2]["idTagInfo"]["status"] == "Accepted"

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
        assert response[2]["idTagInfo"]["status"] == "Accepted"

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
        assert await communicator.receive_json_from() == [3, "meter-1", {}]

        await communicator.send_json_to(
            [
                2,
                "stop-1",
                "StopTransaction",
                {"transactionId": transaction_id, "meterStop": 150},
            ]
        )
        assert await communicator.receive_json_from() == [3, "stop-1", {"idTagInfo": {"status": "Accepted"}}]

        for unique_id, action, payload, expected in (
            ("transfer-1", "DataTransfer", {"vendorId": "ACME"}, {"status": "Accepted"}),
            ("diagnostics-1", "DiagnosticsStatusNotification", {"status": "Uploaded"}, {}),
            ("firmware-1", "FirmwareStatusNotification", {"status": "Downloaded"}, {}),
        ):
            await communicator.send_json_to([2, unique_id, action, payload])
            assert await communicator.receive_json_from() == [3, unique_id, expected]

        await communicator.disconnect()
