"""Focused tests for NotifyEvent action handlers."""

import pytest

from apps.ocpp import store
from apps.ocpp.consumers import CSMSConsumer
from apps.protocols.models import ProtocolCall as ProtocolCallModel


@pytest.mark.anyio
async def test_notify_event_registered_for_ocpp201_and_ocpp21():
    """NotifyEvent handler should remain dual-registered for 2.0.1 and 2.1."""

    consumer = CSMSConsumer(scope={}, receive=None, send=None)
    calls = getattr(consumer._handle_notify_event_action, "__protocol_calls__", set())
    assert ("ocpp201", ProtocolCallModel.CP_TO_CSMS, "NotifyEvent") in calls
    assert ("ocpp21", ProtocolCallModel.CP_TO_CSMS, "NotifyEvent") in calls


@pytest.mark.anyio
async def test_notify_event_forwards_observability_payload(monkeypatch):
    """NotifyEvent should normalize and forward event payload fields."""

    consumer = CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = store.identity_key("OBS-1", 1)
    consumer.charger_id = "OBS-1"
    consumer.connector_value = 1

    forwarded: list[dict[str, object]] = []
    monkeypatch.setattr(
        store,
        "forward_event_to_observability",
        lambda payload: forwarded.append(payload),
    )

    payload = {
        "generatedAt": "2024-01-01T00:00:00Z",
        "seqNo": 9,
        "tbc": True,
        "eventData": [
            {
                "eventId": "7",
                "timestamp": "2024-01-01T00:00:05Z",
                "eventType": "Alert",
                "trigger": "Delta",
                "actualValue": "85C",
                "cause": "Overheat",
                "techCode": "TMP",
                "techInfo": "Sensor drift",
                "cleared": False,
                "severity": "1",
                "transactionId": "TX-9",
                "variableMonitoringId": "3",
                "component": {
                    "name": "Temperature",
                    "instance": "core",
                    "evse": {"id": 2, "connectorId": 1},
                },
                "variable": {"name": "Temp", "instance": "A"},
            }
        ],
    }

    result = await consumer._handle_notify_event_action(payload, "evt-msg-1", "", "")

    assert result == {}
    assert forwarded
    event = forwarded[0]
    assert event["charger_id"] == "OBS-1"
    assert event["connector_id"] == "1"
    assert event["evse_id"] == 2
    assert event["event_id"] == 7


@pytest.mark.anyio
async def test_notify_event_requires_event_data(monkeypatch):
    """NotifyEvent should safely ignore payloads without eventData."""

    consumer = CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = "OBS-2"
    consumer.charger_id = "OBS-2"

    forwarded: list[dict[str, object]] = []
    monkeypatch.setattr(
        store,
        "forward_event_to_observability",
        lambda payload: forwarded.append(payload),
    )

    result = await consumer._handle_notify_event_action({"seqNo": 1}, "evt-msg-2", "", "")

    assert result == {}
    assert forwarded == []
