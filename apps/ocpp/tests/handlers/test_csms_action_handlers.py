import pytest
from channels.db import database_sync_to_async

from apps.cards.models import RFID
from apps.features.models import Feature
from apps.ocpp import store
from apps.ocpp.consumers.csms.consumer import CSMSConsumer
from apps.ocpp.models import (
    Charger,
    ClearedChargingLimitEvent,
    DisplayMessage,
    DisplayMessageNotification,
    MonitoringRule,
)


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_authorize_handler_contract_uses_existing_energy_account_rules():
    charger = await database_sync_to_async(Charger.objects.create)(
        charger_id="CP-EA-HANDLER",
        require_rfid=True,
    )
    await database_sync_to_async(Feature.objects.update_or_create)(
        slug="energy-accounts",
        defaults={
            "display": "Energy Accounts",
            "is_enabled": True,
            "metadata": {"parameters": {"energy_credits_required": "disabled"}},
        },
    )
    await database_sync_to_async(RFID.objects.create)(rfid="EA-H-001", allowed=True)

    consumer = CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = store.identity_key(charger.charger_id, 1)
    consumer.charger_id = charger.charger_id
    consumer.charger = charger
    consumer.aggregate_charger = None

    result = await consumer._action_handler("Authorize").handle(
        {"idTag": "EA-H-001"}, "msg-auth", "", ""
    )

    assert result["idTagInfo"]["status"] == "Invalid"


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_cleared_charging_limit_handler_contract_persists_event():
    charger = await database_sync_to_async(Charger.objects.create)(charger_id="CP-HCLR")
    consumer = CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = "CP-HCLR"
    consumer.charger_id = charger.charger_id
    consumer.charger = charger
    consumer.aggregate_charger = None

    result = await consumer._action_handler("ClearedChargingLimit").handle(
        {"evseId": 3, "chargingLimitSource": "EMS"}, "msg-clear", "", ""
    )

    assert result == {}
    event = await database_sync_to_async(ClearedChargingLimitEvent.objects.get)(
        charger=charger
    )
    assert event.evse_id == 3
    assert event.charging_limit_source == "EMS"


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_notify_charging_limit_handler_contract_updates_charger():
    charger = await database_sync_to_async(Charger.objects.create)(
        charger_id="CP-HLIMIT",
        connector_id=1,
    )
    consumer = CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = "CP-HLIMIT#1"
    consumer.charger_id = charger.charger_id
    consumer.charger = charger
    consumer.aggregate_charger = None
    consumer.connector_value = 1

    payload = {
        "chargingLimit": {"chargingLimitSource": "EMS", "isGridCritical": True},
        "chargingSchedule": [
            {
                "id": 1,
                "chargingRateUnit": "A",
                "chargingSchedulePeriod": [{"startPeriod": 0, "limit": 16}],
            }
        ],
        "evseId": 5,
    }
    result = await consumer._action_handler("NotifyChargingLimit").handle(
        payload, "msg-limit", "", ""
    )

    assert result == {}
    refreshed = await database_sync_to_async(Charger.objects.get)(pk=charger.pk)
    assert refreshed.last_charging_limit_source == "EMS"
    assert refreshed.last_charging_limit["evseId"] == 5


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_notify_display_messages_handler_contract_persists_messages():
    charger = await database_sync_to_async(Charger.objects.create)(charger_id="CP-HDISP")
    consumer = CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = "CP-HDISP"
    consumer.charger_id = charger.charger_id
    consumer.charger = charger
    consumer.aggregate_charger = None

    payload = {
        "requestId": 9,
        "tbc": False,
        "messageInfo": [
            {
                "messageId": 101,
                "priority": "High",
                "state": "Active",
                "validFrom": "2024-01-01T00:00:00Z",
                "validTo": "2024-01-02T00:00:00Z",
                "message": {"content": "Hello", "language": "en"},
                "component": {"name": "Display", "instance": "1"},
                "variable": {"name": "Content", "instance": "main"},
            }
        ],
    }

    result = await consumer._action_handler("NotifyDisplayMessages").handle(
        payload, "msg-display", "", ""
    )

    assert result == {}
    notification = await database_sync_to_async(DisplayMessageNotification.objects.get)(
        charger=charger, request_id=9
    )
    message = await database_sync_to_async(DisplayMessage.objects.get)(
        charger=charger, message_id=101
    )
    assert notification.completed_at is not None
    assert message.content == "Hello"


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_notify_monitoring_report_handler_contract_persists_rules():
    charger = await database_sync_to_async(Charger.objects.create)(
        charger_id="CP-HMON",
        connector_id=1,
    )
    consumer = CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = "CP-HMON#1"
    consumer.charger_id = charger.charger_id
    consumer.charger = charger
    consumer.aggregate_charger = None
    consumer.connector_value = 1

    payload = {
        "requestId": 51,
        "seqNo": 1,
        "generatedAt": "2024-01-01T00:00:00Z",
        "tbc": False,
        "monitoringData": [
            {
                "component": {"name": "EVSE", "instance": "1", "evse": {"id": 1}},
                "variable": {"name": "Voltage", "instance": "L1"},
                "variableMonitoring": [
                    {"id": 9001, "severity": 7, "type": "UpperThreshold", "value": "250"}
                ],
            }
        ],
    }

    result = await consumer._action_handler("NotifyMonitoringReport").handle(
        payload, "msg-monitor", "", ""
    )

    assert result == {}
    rule = await database_sync_to_async(MonitoringRule.objects.get)(
        charger=charger,
        monitoring_id=9001,
    )
    assert rule.monitor_type == "UpperThreshold"
    assert rule.threshold == "250"
