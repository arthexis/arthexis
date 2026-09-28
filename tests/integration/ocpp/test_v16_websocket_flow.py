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

async def _call(communicator, unique_id: str, action: str, payload: dict) -> dict:
    await communicator.send_json_to([2, unique_id, action, payload])
    response = await communicator.receive_json_from()
    assert response[:2] == [3, unique_id]
    return response[2]


async def _boot(communicator, unique_id: str) -> None:
    response = await _call(
        communicator,
        unique_id,
        "BootNotification",
        {"chargePointVendor": "ACME", "chargePointModel": "Test"},
    )
    assert response["status"] == "Accepted"


async def _authorize(communicator, unique_id: str) -> None:
    response = await _call(
        communicator,
        unique_id,
        "Authorize",
        {"idTag": "card-tag"},
    )
    assert response["idTagInfo"]["status"] == "Accepted"


async def _meter_value(
    communicator,
    unique_id: str,
    *,
    transaction_id: int,
    timestamp: str,
    value: str,
) -> None:
    response = await _call(
        communicator,
        unique_id,
        "MeterValues",
        {
            "transactionId": transaction_id,
            "meterValue": [
                {
                    "timestamp": timestamp,
                    "sampledValue": [{"value": value}],
                }
            ],
        },
    )
    assert response == {}



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


    def test_backlog_reconnect_resumes_transaction_over_websocket(self) -> None:
        async_to_sync(self._run_backlog_reconnect_exchange)()

        transaction = OcppTransaction.objects.get()
        assert transaction.stopped_at is not None
        assert transaction.meter_values.count() == 2
        assert list(
            transaction.meter_values.order_by("sampled_at", "id").values_list(
                "value", flat=True
            )
        ) == [Decimal("125"), Decimal("150")]

    async def _run_backlog_reconnect_exchange(self) -> None:
        first = await connect_charger()
        await _boot(first, "boot-backlog-1")
        await _authorize(first, "authorize-backlog-1")

        started = await _call(
            first,
            "start-backlog-1",
            "StartTransaction",
            {
                "connectorId": 1,
                "idTag": "card-tag",
                "meterStart": 100,
                "timestamp": "2026-01-01T00:00:00Z",
            },
        )
        assert started["idTagInfo"]["status"] == "Accepted"
        transaction_id = started["transactionId"]

        await _meter_value(
            first,
            "meter-backlog-1",
            transaction_id=transaction_id,
            timestamp="2026-01-01T00:05:00Z",
            value="125",
        )
        await first.disconnect()

        second = await connect_charger()
        await _boot(second, "boot-backlog-2")
        heartbeat = await _call(second, "heartbeat-backlog-2", "Heartbeat", {})
        assert "currentTime" in heartbeat

        await _meter_value(
            second,
            "meter-backlog-replay",
            transaction_id=transaction_id,
            timestamp="2026-01-01T00:05:00Z",
            value="125",
        )
        await _meter_value(
            second,
            "meter-backlog-2",
            transaction_id=transaction_id,
            timestamp="2026-01-01T00:10:00Z",
            value="150",
        )

        stopped = await _call(
            second,
            "stop-backlog-2",
            "StopTransaction",
            {
                "transactionId": transaction_id,
                "meterStop": 175,
                "timestamp": "2026-01-01T00:15:00Z",
            },
        )
        assert stopped == {"idTagInfo": {"status": "Accepted"}}
        await second.disconnect()

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
        await _boot(first, "boot-recovery-1")
        await _authorize(first, "authorize-recovery-1")

        started = await _call(
            first,
            "start-recovery-1",
            "StartTransaction",
            {"connectorId": 1, "idTag": "card-tag", "meterStart": 100},
        )
        assert started["idTagInfo"]["status"] == "Accepted"
        await first.disconnect()

        second = await connect_charger()
        await _boot(second, "boot-recovery-2")
        status = await _call(
            second,
            "status-recovery-available",
            "StatusNotification",
            {
                "connectorId": 1,
                "status": "Available",
                "timestamp": "2099-01-01T00:00:00Z",
            },
        )
        assert status == {}
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
