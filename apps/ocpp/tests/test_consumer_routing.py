"""Tests for OCPP consumer action dispatch and extracted handler adapters."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from apps.ocpp import store
from apps.ocpp.consumers import CSMSConsumer
from apps.ocpp.consumers.base.routing import ActionRouter
from apps.ocpp.consumers.csms import consumer as csms_consumer
from apps.ocpp.models import Transaction


@pytest.fixture(autouse=True)
def reset_store_state():
    """Reset in-memory store state used by dispatch tests."""

    store.logs["charger"].clear()
    yield
    store.logs["charger"].clear()

@pytest.mark.anyio
async def test_action_router_resolves_transaction_and_notification_handlers():
    """Router exposes explicit registry entries for high-risk actions."""

    consumer = CSMSConsumer(scope={}, receive=None, send=None)
    router = ActionRouter(consumer)

    assert router.resolve("TransactionEvent") == consumer._handle_transaction_event_action
    assert router.resolve("MeterValues") == consumer._handle_meter_values_action
    assert (
        router.resolve("PublishFirmwareStatusNotification")
        == consumer._handle_publish_firmware_status_notification_action
    )
    assert (
        router.resolve("FirmwareStatusNotification")
        == consumer._handle_firmware_status_notification_action
    )

@pytest.mark.anyio
async def test_dispatch_routes_via_registry_for_transaction_event():
    """Dispatch uses the explicit action registry from routing.py."""

    consumer = CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = "CP-ROUTE"
    consumer.charger_id = "CP-ROUTE"
    consumer._log_triggered_follow_up = lambda *_args, **_kwargs: None
    consumer._assign_connector = AsyncMock()
    consumer._forward_charge_point_message = AsyncMock()
    consumer._handle_transaction_event_action = AsyncMock(return_value={"idTokenInfo": {}})
    consumer.send = AsyncMock()

    msg = [2, "msg-1", "TransactionEvent", {"connectorId": 1}]
    await consumer._handle_call_message(msg, json.dumps(msg), json.dumps(msg))

    consumer._handle_transaction_event_action.assert_awaited_once()
    consumer.send.assert_awaited_once()


@pytest.mark.anyio
async def test_ocpp21_cp_to_csms_actions_resolve_to_concrete_handlers():
    """OCPP 2.1 CP->CSMS actions should resolve via router, not empty fallthrough."""

    consumer = CSMSConsumer(scope={}, receive=None, send=None)
    router = ActionRouter(consumer)

    expected_bindings = {
        "BootNotification": consumer._handle_boot_notification_action,
        "DataTransfer": consumer._handle_data_transfer_action,
        "Heartbeat": consumer._handle_heartbeat_action,
        "LogStatusNotification": consumer._handle_log_status_notification_action,
        "MeterValues": consumer._handle_meter_values_action,
        "FirmwareStatusNotification": consumer._handle_firmware_status_notification_action,
        "NotifyChargingLimit": consumer._action_handler("NotifyChargingLimit").handle,
        "NotifyCustomerInformation": consumer._handle_notify_customer_information_action,
        "NotifyDisplayMessages": consumer._action_handler("NotifyDisplayMessages").handle,
        "NotifyEVChargingNeeds": consumer._handle_notify_ev_charging_needs_action,
        "NotifyEVChargingSchedule": consumer._handle_notify_ev_charging_schedule_action,
        "PublishFirmwareStatusNotification": consumer._handle_publish_firmware_status_notification_action,
        "ReportChargingProfiles": consumer._handle_report_charging_profiles_action,
        "SecurityEventNotification": consumer._handle_security_event_notification_action,
        "StatusNotification": consumer._handle_status_notification_action,
    }

    for action, handler in expected_bindings.items():
        resolved = router.resolve(action)
        assert resolved is not None
        assert getattr(resolved, "__name__", "") == getattr(handler, "__name__", "")
        assert "stub" not in getattr(resolved, "__qualname__", "").casefold()


def test_status_notification_normalization_maps_ocpp21_fields():
    consumer = CSMSConsumer(scope={}, receive=None, send=None)

    payload = {
        "connectorStatus": "Occupied",
        "evse": {"id": 3},
        "statusInfo": {"reasonCode": "InternalError", "additionalInfo": "door-open"},
        "timestamp": "2026-01-01T00:00:00Z",
    }

    normalized = consumer._normalized_status_notification_payload(payload)

    assert normalized["status"] == "Occupied"
    assert normalized["connectorId"] == 3
    assert normalized["errorCode"] == "NoError"
    assert normalized["vendorId"] == "InternalError"
    assert normalized["info"] == "door-open"


def test_meter_values_normalization_maps_ocpp21_evse_to_connector_id():
    consumer = CSMSConsumer(scope={}, receive=None, send=None)

    normalized = consumer._normalized_meter_values_payload(
        {"evse": {"id": "4"}, "meterValue": []}
    )

    assert normalized["connectorId"] == "4"


def test_meter_values_normalization_preserves_zero_connector_id():
    consumer = CSMSConsumer(scope={}, receive=None, send=None)

    normalized = consumer._normalized_meter_values_payload(
        {"evse": {"connectorId": 0, "id": 9}, "evseId": 3, "meterValue": []}
    )

    assert normalized["connectorId"] == 0


def test_status_notification_normalization_preserves_zero_connector_id():
    consumer = CSMSConsumer(scope={}, receive=None, send=None)

    normalized = consumer._normalized_status_notification_payload(
        {"evse": {"connectorId": 0, "id": 9}, "evseId": 3}
    )

    assert normalized["connectorId"] == 0


@pytest.mark.anyio
async def test_store_meter_values_resolves_non_numeric_transaction_id_from_db(monkeypatch):
    consumer = CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = "CP-TX"
    consumer.charger = SimpleNamespace(id=10)
    consumer._assign_connector = AsyncMock()
    consumer._ensure_ocpp_transaction_identifier = AsyncMock()
    consumer._process_meter_value_entries = AsyncMock()

    resolved = SimpleNamespace(pk=42, ocpp_transaction_id="tx-uuid-42")
    lookup = AsyncMock(return_value=resolved)
    monkeypatch.setattr(Transaction, "aget_by_ocpp_id", lookup)

    store.transactions.pop(consumer.store_key, None)
    payload = {"connectorId": 1, "transactionId": "tx-uuid-42", "meterValue": []}
    await consumer._store_meter_values(payload, raw_message='[2, "id", "MeterValues", {}]')

    lookup.assert_awaited_once_with(consumer.charger, "tx-uuid-42")
    assert store.transactions[consumer.store_key] is resolved


@pytest.mark.anyio
async def test_store_meter_values_creates_non_numeric_transaction_id_when_missing(monkeypatch):
    consumer = CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = "CP-TX-MISS"
    consumer.charger = SimpleNamespace(id=11)
    consumer._assign_connector = AsyncMock()
    consumer._ensure_ocpp_transaction_identifier = AsyncMock()
    consumer._process_meter_value_entries = AsyncMock()

    lookup = AsyncMock(return_value=None)
    monkeypatch.setattr(Transaction, "aget_by_ocpp_id", lookup)

    def fake_database_sync_to_async(sync_fn):
        async def wrapped(*args, **kwargs):
            return sync_fn(*args, **kwargs)

        return wrapped

    monkeypatch.setattr(csms_consumer, "database_sync_to_async", fake_database_sync_to_async)

    created = SimpleNamespace(pk=501, ocpp_transaction_id="tx-uuid-501")
    create_mock = Mock(return_value=created)
    monkeypatch.setattr(Transaction.objects, "create", create_mock)

    store.transactions.pop(consumer.store_key, None)
    payload = {"connectorId": 1, "transactionId": "tx-uuid-501", "meterValue": []}
    await consumer._store_meter_values(payload, raw_message='[2, "id", "MeterValues", {}]')

    lookup.assert_awaited_once_with(consumer.charger, "tx-uuid-501")
    create_mock.assert_called_once()
    assert store.transactions[consumer.store_key] is created
