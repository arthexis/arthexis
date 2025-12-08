from unittest.mock import AsyncMock

import pytest

from apps.ocpp import consumers, store
from apps.protocols.models import ProtocolCall as ProtocolCallModel


@pytest.fixture(autouse=True)
def reset_store(monkeypatch, tmp_path):
    store.logs["charger"].clear()
    store.log_names["charger"].clear()
    monkeypatch.setattr(store, "LOG_DIR", tmp_path)
    yield
    store.logs["charger"].clear()
    store.log_names["charger"].clear()


@pytest.mark.anyio
async def test_cleared_charging_limit_logs_payload():
    consumer = consumers.CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = "CP-201"

    result = await consumer._handle_cleared_charging_limit_action(
        {"evseId": 1}, "msg-1", "", ""
    )

    assert result == {}
    entries = list(store.logs["charger"][consumer.store_key])
    assert any("ClearedChargingLimit" in entry for entry in entries)
    assert any("evseId" in entry for entry in entries)


@pytest.mark.anyio
async def test_notify_report_logs_payload():
    consumer = consumers.CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = "CP-201"

    result = await consumer._handle_notify_report_action({"count": 2}, "msg-2", "", "")

    assert result == {}
    entries = list(store.logs["charger"][consumer.store_key])
    assert any("NotifyReport" in entry for entry in entries)
    assert any("count" in entry for entry in entries)


@pytest.mark.anyio
async def test_transaction_event_registered_for_ocpp201():
    consumer = consumers.CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = "CP-201"
    consumer.charger_id = "CP-201"

    async def fake_assign(connector):
        consumer.connector_value = connector

    consumer._assign_connector = AsyncMock(side_effect=fake_assign)

    result = await consumer._handle_transaction_event_action(
        {"eventType": "Other", "evse": {"id": 5}}, "msg-3", "", ""
    )

    assert result == {}
    consumer._assign_connector.assert_awaited()
    calls = getattr(consumer._handle_transaction_event_action, "__protocol_calls__", set())
    assert (
        "ocpp201",
        ProtocolCallModel.CP_TO_CSMS,
        "TransactionEvent",
    ) in calls
