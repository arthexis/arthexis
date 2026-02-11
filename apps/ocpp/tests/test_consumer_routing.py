"""Tests for OCPP consumer action dispatch and extracted handler adapters."""

import json
from unittest.mock import AsyncMock

import pytest

from apps.ocpp import store
from apps.ocpp.consumers import CSMSConsumer
from apps.ocpp.consumers.base.consumer.routing import ActionRouter


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


@pytest.mark.anyio
async def test_dispatch_routes_via_registry_for_transaction_event():
    """Dispatch uses the explicit action registry from routing.py."""

    consumer = CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = "CP-ROUTE"
    consumer.charger_id = "CP-ROUTE"
    consumer._log_triggered_follow_up = lambda *args, **kwargs: None
    consumer._assign_connector = AsyncMock()
    consumer._forward_charge_point_message = AsyncMock()
    consumer._handle_transaction_event_action = AsyncMock(return_value={"idTokenInfo": {}})
    consumer.send = AsyncMock()

    msg = [2, "msg-1", "TransactionEvent", {"connectorId": 1}]
    await consumer._handle_call_message(msg, json.dumps(msg), json.dumps(msg))

    consumer._handle_transaction_event_action.assert_awaited_once()
    consumer.send.assert_awaited_once()


@pytest.mark.anyio
async def test_wrapped_high_risk_handlers_delegate_to_legacy_methods():
    """Wrapper handlers delegate to legacy implementations where DB writes occur."""

    consumer = CSMSConsumer(scope={}, receive=None, send=None)

    consumer._handle_transaction_event_legacy = AsyncMock(return_value={})
    consumer._handle_meter_values_legacy = AsyncMock(return_value={})
    consumer._handle_publish_firmware_status_notification_action_legacy = AsyncMock(return_value={})
    consumer._handle_log_status_notification_action_legacy = AsyncMock(return_value={})

    await consumer._handle_transaction_event_action({}, "msg-1", "raw", "raw")
    await consumer._handle_meter_values_action({}, "msg-2", "raw", "raw")
    await consumer._handle_publish_firmware_status_notification_action({}, "msg-3", "raw", "raw")
    await consumer._handle_log_status_notification_action({}, "msg-4", "raw", "raw")

    consumer._handle_transaction_event_legacy.assert_awaited_once()
    consumer._handle_meter_values_legacy.assert_awaited_once()
    consumer._handle_publish_firmware_status_notification_action_legacy.assert_awaited_once()
    consumer._handle_log_status_notification_action_legacy.assert_awaited_once()
