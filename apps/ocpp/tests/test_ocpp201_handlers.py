import json
from datetime import datetime, timezone as dt_timezone
from functools import partial
from unittest.mock import AsyncMock
import json

import anyio
import pytest
from channels.db import database_sync_to_async
from django.utils import timezone

from apps.ocpp import consumers, store, call_error_handlers, call_result_handlers
from apps.flows.models import Transition
from apps.ocpp.views import actions
from apps.ocpp.views.common import ActionContext
from apps.ocpp.models import (
    Charger,
    CPReservation,
    CertificateRequest,
    CertificateOperation,
    CertificateStatusCheck,
    InstalledCertificate,
    CostUpdate,
    ChargingProfile,
    ChargingSchedule,
    Transaction,
    Variable,
    MonitoringRule,
    MonitoringReport,
    DeviceInventorySnapshot,
    DeviceInventoryItem,
    CustomerInformationRequest,
    CustomerInformationChunk,
    DisplayMessageNotification,
    DisplayMessage,
    ClearedChargingLimitEvent,
    CPFirmware,
    CPFirmwareDeployment,
)
from apps.protocols.models import ProtocolCall as ProtocolCallModel
from apps.maps.models import Location
from django.utils.dateparse import parse_datetime


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def reset_store(monkeypatch, tmp_path):
    store.logs["charger"].clear()
    store.log_names["charger"].clear()
    store.transaction_requests.clear()
    store._transaction_requests_by_connector.clear()
    store._transaction_requests_by_transaction.clear()
    store.billing_updates.clear()
    store.ev_charging_needs.clear()
    store.ev_charging_schedules.clear()
    store.planner_notifications.clear()
    store.connector_release_notifications.clear()
    store.observability_events.clear()
    store.transaction_events.clear()
    store.monitoring_reports.clear()
    store.clear_display_message_compliance()
    store.charging_profile_reports.clear()
    log_dir = tmp_path / "logs"
    session_dir = log_dir / "sessions"
    lock_dir = tmp_path / "locks"
    session_dir.mkdir(parents=True, exist_ok=True)
    lock_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(store, "LOG_DIR", log_dir)
    monkeypatch.setattr(store, "SESSION_DIR", session_dir)
    monkeypatch.setattr(store, "LOCK_DIR", lock_dir)
    monkeypatch.setattr(store, "SESSION_LOCK", lock_dir / "charging.lck")
    yield
    store.logs["charger"].clear()
    store.log_names["charger"].clear()
    store.transaction_requests.clear()
    store._transaction_requests_by_connector.clear()
    store._transaction_requests_by_transaction.clear()
    store.billing_updates.clear()
    store.ev_charging_needs.clear()
    store.ev_charging_schedules.clear()
    store.planner_notifications.clear()
    store.connector_release_notifications.clear()
    store.observability_events.clear()
    store.transaction_events.clear()
    store.monitoring_reports.clear()
    store.charging_profile_reports.clear()


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _reset_pending_calls() -> None:
    store.pending_calls.clear()
    store._pending_call_events.clear()
    store._pending_call_results.clear()
    for handle in store._pending_call_handles.values():
        try:
            handle.cancel()
        except Exception:
            pass
    store._pending_call_handles.clear()


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_handle_clear_charging_profile_result_updates_profile():
    charger = await database_sync_to_async(Charger.objects.create)(charger_id="CLR-CP-1")
    profile = ChargingProfile(
        charger=charger,
        connector_id=1,
        charging_profile_id=9,
        stack_level=1,
        purpose=ChargingProfile.Purpose.CHARGE_POINT_MAX_PROFILE,
        kind=ChargingProfile.Kind.ABSOLUTE,
    )
    await database_sync_to_async(ChargingProfile.objects.bulk_create)([profile])
    consumer = consumers.CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = "CLR-CP-1"
    consumer.charger_id = charger.charger_id
    payload = {"status": "Accepted", "statusInfo": {"detail": "ok"}}
    metadata = {"charging_profile_id": 9, "charger_id": charger.charger_id}

    result = await call_result_handlers.handle_clear_charging_profile_result(
        consumer,
        "msg-clear-1",
        metadata,
        payload,
        consumer.store_key,
    )

    assert result is True
    updated = await database_sync_to_async(ChargingProfile.objects.get)(
        charger=charger, charging_profile_id=9
    )
    assert updated.last_status == "Accepted"
    assert updated.last_status_info == "{\"detail\": \"ok\"}"
    assert updated.last_response_payload.get("status") == "Accepted"
    assert "msg-clear-1" in store._pending_call_results
    logs = list(store.logs["charger"].get(consumer.store_key, []))
    assert any("ClearChargingProfile result" in entry for entry in logs)


@pytest.mark.anyio
async def test_set_monitoring_base_result_clears_pending_call():
    _reset_pending_calls()
    consumer = consumers.CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = "CP-MON-BASE"
    consumer.charger_id = consumer.store_key
    message_id = "msg-monitoring-base"
    metadata = {"action": "SetMonitoringBase", "charger_id": consumer.charger_id, "log_key": consumer.store_key}

    store.register_pending_call(message_id, metadata)

    await consumer._handle_call_result(
        message_id,
        {"status": "Accepted", "statusInfo": {"detail": "applied"}},
    )

    assert message_id not in store.pending_calls
    result = store.wait_for_pending_call(message_id, timeout=0.5)
    assert result is not None
    assert result.get("success") is True
    assert (result.get("payload") or {}).get("status") == "Accepted"


@pytest.mark.anyio
async def test_set_monitoring_level_error_clears_pending_call():
    _reset_pending_calls()
    consumer = consumers.CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = "CP-MON-LEVEL"
    consumer.charger_id = consumer.store_key
    message_id = "msg-monitoring-level"
    metadata = {"action": "SetMonitoringLevel", "charger_id": consumer.charger_id, "log_key": consumer.store_key}

    store.register_pending_call(message_id, metadata)

    await consumer._handle_call_error(
        message_id,
        "InternalError",
        "unable to apply",
        {"detail": "test"},
    )

    assert message_id not in store.pending_calls
    result = store.wait_for_pending_call(message_id, timeout=0.5)
    assert result is not None
    assert result.get("success") is False
    assert result.get("error_code") == "InternalError"


@pytest.mark.anyio
async def test_set_monitoring_base_result_clears_pending_call_from_action():
    _reset_pending_calls()

    class DummyWebSocket:
        def __init__(self):
            self.sent: list[str] = []

        async def send(self, message: str) -> None:  # pragma: no cover - async wrapper
            self.sent.append(message)

    ws = DummyWebSocket()
    log_key = store.identity_key("CP-MON-ACT", None)
    context = ActionContext("CP-MON-ACT", None, charger=None, ws=ws, log_key=log_key)
    action_call = await anyio.to_thread.run_sync(
        lambda: actions._handle_set_monitoring_base(
            context, {"monitoringBase": "All"}
        )
    )

    assert isinstance(action_call, actions.ActionCall)
    assert len(ws.sent) == 1
    message = json.loads(ws.sent[0])
    message_id = message[1]

    consumer = consumers.CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = log_key
    consumer.charger_id = context.cid

    await consumer._handle_call_result(message_id, {"status": "Accepted"})

    assert message_id not in store.pending_calls
    result = store.wait_for_pending_call(message_id, timeout=0.5)
    assert result is not None
    assert result.get("success") is True
    assert result.get("metadata", {}).get("monitoring_base") == "All"


@pytest.mark.anyio
async def test_set_monitoring_level_error_clears_pending_call_from_action():
    _reset_pending_calls()

    class DummyWebSocket:
        def __init__(self):
            self.sent: list[str] = []

        async def send(self, message: str) -> None:  # pragma: no cover - async wrapper
            self.sent.append(message)

    ws = DummyWebSocket()
    log_key = store.identity_key("CP-MON-ERR", None)
    context = ActionContext("CP-MON-ERR", None, charger=None, ws=ws, log_key=log_key)
    action_call = await anyio.to_thread.run_sync(
        lambda: actions._handle_set_monitoring_level(context, {"severity": 3})
    )

    assert isinstance(action_call, actions.ActionCall)
    assert len(ws.sent) == 1
    message = json.loads(ws.sent[0])
    message_id = message[1]

    consumer = consumers.CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = log_key
    consumer.charger_id = context.cid

    await consumer._handle_call_error(
        message_id,
        "InternalError",
        "unable to apply",
        {"detail": "test"},
    )

    assert message_id not in store.pending_calls
    result = store.wait_for_pending_call(message_id, timeout=0.5)
    assert result is not None
    assert result.get("success") is False
    assert result.get("error_code") == "InternalError"
    assert result.get("metadata", {}).get("monitoring_level") == 3


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_unlock_connector_result_updates_state():
    _reset_pending_calls()

    class DummyWebSocket:
        def __init__(self):
            self.sent: list[str] = []

        async def send(self, message: str) -> None:  # pragma: no cover - async wrapper
            self.sent.append(message)

    charger = await database_sync_to_async(Charger.objects.create)(
        charger_id="CP-UNLOCK-1", connector_id=2
    )
    log_key = store.identity_key(charger.charger_id, charger.connector_id)
    ws = DummyWebSocket()
    context = ActionContext(
        charger.charger_id, charger.connector_id, charger=charger, ws=ws, log_key=log_key
    )
    action_call = await anyio.to_thread.run_sync(
        lambda: actions._handle_unlock_connector(context, {})
    )

    consumer = consumers.CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = log_key
    consumer.charger_id = charger.charger_id
    consumer.charger = charger
    consumer.aggregate_charger = None

    payload = {"status": "Unlocked", "statusInfo": {"detail": "released"}}
    await consumer._handle_call_result(action_call.message_id, payload)

    assert action_call.message_id not in store.pending_calls
    updated = await database_sync_to_async(Charger.objects.get)(pk=charger.pk)
    assert updated.availability_request_status == "Unlocked"
    assert updated.availability_request_details == '{"detail": "released"}'
    result = store.wait_for_pending_call(action_call.message_id, timeout=0.5)
    assert result is not None
    assert result.get("success") is True
    assert (result.get("payload") or {}).get("status") == "Unlocked"


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_unlock_connector_error_records_failure():
    _reset_pending_calls()

    class DummyWebSocket:
        def __init__(self):
            self.sent: list[str] = []

        async def send(self, message: str) -> None:  # pragma: no cover - async wrapper
            self.sent.append(message)

    charger = await database_sync_to_async(Charger.objects.create)(
        charger_id="CP-UNLOCK-2", connector_id=3
    )
    log_key = store.identity_key(charger.charger_id, charger.connector_id)
    ws = DummyWebSocket()
    context = ActionContext(
        charger.charger_id, charger.connector_id, charger=charger, ws=ws, log_key=log_key
    )
    action_call = await anyio.to_thread.run_sync(
        lambda: actions._handle_unlock_connector(context, {})
    )

    consumer = consumers.CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = log_key
    consumer.charger_id = charger.charger_id
    consumer.charger = charger
    consumer.aggregate_charger = None

    await consumer._handle_call_error(
        action_call.message_id,
        "InternalError",
        "unable to unlock",
        {"detail": "test"},
    )

    assert action_call.message_id not in store.pending_calls
    updated = await database_sync_to_async(Charger.objects.get)(pk=charger.pk)
    assert updated.availability_request_status == "Rejected"
    assert updated.availability_request_details == '{"detail": "test"}'
    result = store.wait_for_pending_call(action_call.message_id, timeout=0.5)
    assert result is not None
    assert result.get("success") is False
    assert result.get("error_code") == "InternalError"
    assert result.get("error_description") == "unable to unlock"
    assert result.get("error_details") == {"detail": "test"}


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_handle_clear_charging_profile_error_records_failure():
    charger = await database_sync_to_async(Charger.objects.create)(charger_id="CLR-CP-2")
    profile = ChargingProfile(
        charger=charger,
        connector_id=1,
        charging_profile_id=11,
        stack_level=1,
        purpose=ChargingProfile.Purpose.CHARGE_POINT_MAX_PROFILE,
        kind=ChargingProfile.Kind.ABSOLUTE,
    )
    await database_sync_to_async(ChargingProfile.objects.bulk_create)([profile])
    consumer = consumers.CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = "CLR-CP-2"
    consumer.charger_id = charger.charger_id
    metadata = {"charging_profile_id": 11, "charger_id": charger.charger_id}

    result = await call_error_handlers.handle_clear_charging_profile_error(
        consumer,
        "msg-clear-2",
        metadata,
        "InternalError",
        "failure",
        {"reason": "test"},
        consumer.store_key,
    )

    assert result is True
    updated = await database_sync_to_async(ChargingProfile.objects.get)(
        charger=charger, charging_profile_id=11
    )
    assert updated.last_status == "InternalError"
    assert "failure" in updated.last_status_info
    assert updated.last_response_payload.get("errorCode") == "InternalError"
    assert "msg-clear-2" in store._pending_call_results
    logs = list(store.logs["charger"].get(consumer.store_key, []))
    assert any("ClearChargingProfile error" in entry for entry in logs)


@pytest.mark.anyio
async def test_cleared_charging_limit_logs_payload():
    consumer = consumers.CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = "CP-201"

    calls = getattr(consumer._handle_cleared_charging_limit_action, "__protocol_calls__", set())
    assert ("ocpp201", ProtocolCallModel.CP_TO_CSMS, "ClearedChargingLimit") in calls
    assert ("ocpp21", ProtocolCallModel.CP_TO_CSMS, "ClearedChargingLimit") in calls

    result = await consumer._handle_cleared_charging_limit_action(
        {"evseId": 1, "chargingLimitSource": "EMS"}, "msg-1", "", ""
    )

    assert result == {}
    entries = list(store.logs["charger"][consumer.store_key])
    assert any("ClearedChargingLimit" in entry for entry in entries)
    assert any("evseId" in entry for entry in entries)
    assert any("EMS" in entry for entry in entries)


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_cleared_charging_limit_persists_event():
    charger = await database_sync_to_async(Charger.objects.create)(charger_id="CP-202")
    consumer = consumers.CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = "CP-202"
    consumer.charger = charger
    consumer.aggregate_charger = None

    payload = {"evseId": 3, "chargingLimitSource": "EMS"}

    result = await consumer._handle_cleared_charging_limit_action(
        payload, "msg-2", "", ""
    )

    assert result == {}
    event = await database_sync_to_async(ClearedChargingLimitEvent.objects.get)(
        charger=charger
    )
    assert event.evse_id == 3
    assert event.charging_limit_source == "EMS"
    assert event.ocpp_message_id == "msg-2"
    assert event.raw_payload["evseId"] == 3


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_notify_charging_limit_persists_payload():
    charger = await database_sync_to_async(Charger.objects.create)(
        charger_id="CP-301", connector_id=1
    )
    consumer = consumers.CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = "CP-301#1"
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

    result = await consumer._handle_notify_charging_limit_action(
        payload, "msg-3", "", ""
    )

    assert result == {}
    updated = await database_sync_to_async(Charger.objects.get)(pk=charger.pk)
    assert updated.last_charging_limit.get("evseId") == 5
    assert updated.last_charging_limit_source == "EMS"
    assert updated.last_charging_limit_is_grid_critical is True
    assert updated.last_charging_limit_at is not None
    schedule = updated.last_charging_limit.get("chargingSchedule") or []
    assert isinstance(schedule, list)
    assert schedule[0]["chargingSchedulePeriod"][0]["limit"] == 16
    entries = list(store.logs["charger"].get(consumer.store_key, []))
    assert any("NotifyChargingLimit" in entry for entry in entries)
    assert any("source=EMS" in entry for entry in entries)


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_notify_report_persists_inventory_snapshot():
    charger = await database_sync_to_async(Charger.objects.create)(
        charger_id="INV-201", connector_id=1
    )
    consumer = consumers.CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = "INV-201#1"
    consumer.charger_id = charger.charger_id
    consumer.charger = charger
    consumer.connector_value = 1

    payload = {
        "requestId": 7,
        "seqNo": 2,
        "tbc": False,
        "generatedAt": "2024-01-01T00:00:00Z",
        "reportData": [
            {
                "component": {"name": "EVSE", "instance": "1"},
                "variable": {"name": "Status", "instance": "A"},
                "variableAttribute": [{"type": "Actual", "value": "Available"}],
                "variableCharacteristics": {"dataType": "string"},
            }
        ],
    }

    result = await consumer._handle_notify_report_action(payload, "msg-2", "", "")

    assert result == {}
    snapshot = await database_sync_to_async(DeviceInventorySnapshot.objects.get)(
        charger=charger
    )
    assert snapshot.request_id == 7
    assert snapshot.seq_no == 2
    assert snapshot.generated_at.isoformat() == "2024-01-01T00:00:00+00:00"
    items = await database_sync_to_async(list)(snapshot.items.all())
    assert len(items) == 1
    assert items[0].component_name == "EVSE"
    assert items[0].component_instance == "1"
    assert items[0].variable_name == "Status"
    assert items[0].variable_instance == "A"
    assert items[0].attributes[0]["value"] == "Available"
    assert items[0].characteristics["dataType"] == "string"
    entries = list(store.logs["charger"].get(consumer.store_key, []))
    assert any("NotifyReport" in entry for entry in entries)
    assert any("items=1" in entry for entry in entries)


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_publish_firmware_status_updates_deployment():
    firmware = await database_sync_to_async(CPFirmware.objects.create)(
        name="Test Firmware", payload_json={}
    )
    charger = await database_sync_to_async(Charger.objects.create)(
        charger_id="PUB-201"
    )
    deployment = await database_sync_to_async(CPFirmwareDeployment.objects.create)(
        firmware=firmware,
        charger=charger,
        status="Pending",
        status_timestamp=timezone.now(),
    )
    consumer = consumers.CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = charger.charger_id
    consumer.charger = charger
    consumer.aggregate_charger = None

    download_ts = parse_datetime("2024-01-01T00:00:00Z")
    published_ts = parse_datetime("2024-01-01T02:00:00Z")

    await consumer._handle_publish_firmware_status_notification_action(
        {
            "status": "Downloading",
            "requestId": deployment.pk,
            "statusInfo": "starting",
            "timestamp": download_ts.isoformat().replace("+00:00", "Z"),
        },
        "msg-1",
        "",
        "",
    )

    deployment = await database_sync_to_async(CPFirmwareDeployment.objects.get)(
        pk=deployment.pk
    )
    assert deployment.status == "Downloading"
    assert deployment.status_info == "starting"
    assert deployment.completed_at is None

    await consumer._handle_publish_firmware_status_notification_action(
        {
            "status": "Downloaded",
            "requestId": deployment.pk,
            "statusInfo": "ready",
            "timestamp": download_ts.isoformat().replace("+00:00", "Z"),
        },
        "msg-1",
        "",
        "",
    )

    deployment = await database_sync_to_async(CPFirmwareDeployment.objects.get)(
        pk=deployment.pk
    )
    assert deployment.status == "Downloaded"
    assert deployment.downloaded_at == download_ts

    await consumer._handle_publish_firmware_status_notification_action(
        {
            "status": "Published",
            "requestId": deployment.pk,
            "timestamp": published_ts.isoformat().replace("+00:00", "Z"),
        },
        "msg-1",
        "",
        "",
    )

    deployment = await database_sync_to_async(CPFirmwareDeployment.objects.get)(
        pk=deployment.pk
    )
    assert deployment.status == "Published"
    assert deployment.completed_at is not None


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_notify_report_requires_mandatory_fields():
    consumer = consumers.CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = "INV-MISSING"
    consumer.charger_id = "INV-MISSING"
    consumer.connector_value = None

    payload = {"requestId": 8, "reportData": "invalid"}

    result = await consumer._handle_notify_report_action(payload, "msg-3", "", "")

    assert result == {}
    assert await database_sync_to_async(DeviceInventorySnapshot.objects.count)() == 0
    entries = list(store.logs["charger"].get(consumer.store_key, []))
    assert any("missing generatedAt" in entry for entry in entries)


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_cost_updated_persists_and_forwards():
    charger = await database_sync_to_async(Charger.objects.create)(
        charger_id="COST-1"
    )
    transaction = await database_sync_to_async(Transaction.objects.create)(
        charger=charger,
        start_time=timezone.now(),
        received_start_time=timezone.now(),
        ocpp_transaction_id="TX-1",
    )
    consumer = consumers.CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = store.identity_key(charger.charger_id, 1)
    consumer.charger_id = charger.charger_id
    consumer.charger = charger
    consumer.connector_value = 1

    payload = {
        "transactionId": "TX-1",
        "totalCost": "15.75",
        "currency": "USD",
        "timestamp": "2024-01-01T00:00:00Z",
    }

    result = await consumer._handle_cost_updated_action(payload, "msg-cost", "", "")

    assert result == {}
    cost_update = await database_sync_to_async(CostUpdate.objects.get)(
        charger=charger
    )
    assert cost_update.transaction_id == transaction.pk
    assert cost_update.ocpp_transaction_id == "TX-1"
    assert str(cost_update.total_cost) == "15.750"
    assert cost_update.currency == "USD"
    assert any(
        entry.get("cost_update_id") == cost_update.pk for entry in store.billing_updates
    )


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_cost_updated_rejects_invalid_payload():
    charger = await database_sync_to_async(Charger.objects.create)(
        charger_id="COST-2"
    )
    consumer = consumers.CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = store.identity_key(charger.charger_id, 1)
    consumer.charger_id = charger.charger_id
    consumer.charger = charger
    consumer.connector_value = 1

    result = await consumer._handle_cost_updated_action(
        {"totalCost": "bad"}, "msg-invalid", "", ""
    )

    assert result == {}
    exists = await database_sync_to_async(CostUpdate.objects.filter)(charger=charger)
    assert not await database_sync_to_async(exists.exists)()
    assert not store.billing_updates


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


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_get_15118_ev_certificate_persists_request(monkeypatch):
    charger = await database_sync_to_async(Charger.objects.create)(charger_id="CERT-1")
    consumer = consumers.CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = "CERT-1"
    consumer.charger = charger
    consumer.aggregate_charger = None

    def fake_sign(**kwargs):
        return "EXI-RESPONSE"

    monkeypatch.setattr(
        consumers.certificate_signing, "sign_certificate_request", fake_sign
    )

    payload = {"certificateType": "V2G", "exiRequest": "CSRDATA"}
    result = await consumer._handle_get_15118_ev_certificate_action(
        payload, "msg-1", "", ""
    )

    assert result["status"] == "Accepted"
    assert result["exiResponse"] == "EXI-RESPONSE"
    request = await database_sync_to_async(CertificateRequest.objects.get)(charger=charger)
    assert request.action == CertificateRequest.ACTION_15118
    assert request.csr == "CSRDATA"
    assert request.status == CertificateRequest.STATUS_ACCEPTED
    assert request.signed_certificate == "EXI-RESPONSE"
    assert request.response_payload["status"] == "Accepted"


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_get_certificate_status_persists_check():
    charger = await database_sync_to_async(Charger.objects.create)(charger_id="CERT-2")
    hash_data = {"hashAlgorithm": "SHA256", "issuerNameHash": "abc"}
    await database_sync_to_async(InstalledCertificate.objects.create)(
        charger=charger,
        certificate_type="V2G",
        certificate_hash_data=hash_data,
        status=InstalledCertificate.STATUS_INSTALLED,
    )
    consumer = consumers.CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = "CERT-2"
    consumer.charger = charger
    consumer.aggregate_charger = None

    payload = {"certificateHashData": hash_data}
    result = await consumer._handle_get_certificate_status_action(
        payload, "msg-2", "", "",
    )

    assert result["status"] == "Accepted"
    status_check = await database_sync_to_async(CertificateStatusCheck.objects.get)(
        charger=charger
    )
    assert status_check.status == CertificateStatusCheck.STATUS_ACCEPTED
    assert status_check.certificate_hash_data["hashAlgorithm"] == "SHA256"
    assert status_check.responded_at is not None

@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_get_certificate_status_handles_missing_certificate():
    charger = await database_sync_to_async(Charger.objects.create)(charger_id="CERT-4")
    consumer = consumers.CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = "CERT-4"
    consumer.charger = charger
    consumer.aggregate_charger = None

    payload = {"certificateHashData": {"hashAlgorithm": "SHA256"}}
    result = await consumer._handle_get_certificate_status_action(
        payload, "msg-3", "", "",
    )

    assert result["status"] == "Failed"
    status_check = await database_sync_to_async(CertificateStatusCheck.objects.get)(
        charger=charger
    )
    assert status_check.status == CertificateStatusCheck.STATUS_REJECTED
    assert status_check.status_info == "Certificate not found."


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_sign_certificate_validates_csr(monkeypatch):
    charger = await database_sync_to_async(Charger.objects.create)(charger_id="CERT-3")
    consumer = consumers.CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = "CERT-3"
    consumer.charger = charger
    consumer.aggregate_charger = None

    called = False

    def fake_sign(**kwargs):
        nonlocal called
        called = True
        return "CHAIN"

    monkeypatch.setattr(
        consumers.certificate_signing, "sign_certificate_request", fake_sign
    )

    payload = {"csr": "   ", "certificateType": "V2G"}
    result = await consumer._handle_sign_certificate_action(
        payload, "msg-3", "", ""
    )

    assert result["status"] == "Rejected"
    assert called is False
    request = await database_sync_to_async(CertificateRequest.objects.get)(charger=charger)
    assert request.status == CertificateRequest.STATUS_REJECTED
    assert request.status_info == "CSR payload is missing or invalid."


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_sign_certificate_signs_and_dispatches_certificate(monkeypatch):
    charger = await database_sync_to_async(Charger.objects.create)(charger_id="CERT-4")
    consumer = consumers.CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = "CERT-4"
    consumer.charger = charger
    consumer.aggregate_charger = None

    sent: list[str] = []

    async def fake_send(message):
        sent.append(message)

    consumer.send = fake_send

    def fake_sign(**kwargs):
        return "CERTCHAIN"

    monkeypatch.setattr(
        consumers.certificate_signing, "sign_certificate_request", fake_sign
    )

    payload = {"csr": "CSR-123", "certificateType": "V2G"}
    result = await consumer._handle_sign_certificate_action(
        payload, "msg-4", "", ""
    )

    assert result["status"] == "Accepted"

    request = await database_sync_to_async(CertificateRequest.objects.get)(charger=charger)
    assert request.action == CertificateRequest.ACTION_SIGN
    assert request.csr == "CSR-123"
    assert request.signed_certificate == "CERTCHAIN"
    assert request.status == CertificateRequest.STATUS_PENDING

    operation = await database_sync_to_async(
        CertificateOperation.objects.get
    )(charger=charger, action=CertificateOperation.ACTION_SIGNED)
    assert operation.status == CertificateOperation.STATUS_PENDING

    assert len(sent) == 1
    message = json.loads(sent[0])
    assert message[2] == "CertificateSigned"
    assert message[3]["certificateChain"] == "CERTCHAIN"


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_notify_monitoring_report_persists_data():
    charger = await database_sync_to_async(Charger.objects.create)(charger_id="MON-1")
    consumer = consumers.CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = "MON-1"
    consumer.charger_id = charger.charger_id
    consumer.charger = charger
    consumer.aggregate_charger = None

    payload = {
        "requestId": 42,
        "seqNo": 1,
        "generatedAt": "2024-01-01T00:00:00Z",
        "tbc": False,
        "monitoringData": [
            {
                "component": {"name": "EVSE", "instance": "1"},
                "variable": {"name": "Voltage"},
                "variableMonitoring": [
                    {
                        "id": 101,
                        "severity": 5,
                        "type": "UpperThreshold",
                        "value": "240",
                        "transaction": True,
                    }
                ],
            }
        ],
    }

    result = await consumer._handle_notify_monitoring_report_action(
        payload, "msg-5", "", ""
    )

    assert result == {}
    exists = await database_sync_to_async(
        MonitoringReport.objects.filter(charger=charger, request_id=42).exists
    )()
    assert exists
    variable = await database_sync_to_async(Variable.objects.get)(
        charger=charger,
        component_name="EVSE",
        variable_name="Voltage",
    )
    rule = await database_sync_to_async(MonitoringRule.objects.get)(
        charger=charger, monitoring_id=101
    )
    assert rule.variable_id == variable.pk
    assert rule.threshold == "240"


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_notify_monitoring_report_records_analytics():
    charger = await database_sync_to_async(Charger.objects.create)(charger_id="MON-2")
    consumer = consumers.CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = "MON-2"
    consumer.charger_id = charger.charger_id
    consumer.charger = charger
    consumer.aggregate_charger = None

    payload = {
        "requestId": 99,
        "seqNo": "3",
        "generatedAt": "2024-02-02T10:00:00Z",
        "tbc": True,
        "monitoringData": [
            {
                "component": {
                    "name": "Meter", "instance": "main", "evse": {"id": 4, "connectorId": 2}
                },
                "variable": {"name": "Energy", "instance": "A"},
                "variableMonitoring": [
                    {
                        "id": "202",
                        "severity": "3",
                        "type": "LowerThreshold",
                        "value": 10,
                        "transaction": False,
                    }
                ],
            }
        ],
    }

    result = await consumer._handle_notify_monitoring_report_action(
        payload, "msg-analytics", "", ""
    )

    assert result == {}
    assert len(store.monitoring_reports) == 1
    record = store.monitoring_reports[-1]
    assert record["charger_id"] == "MON-2"
    assert record["request_id"] == 99
    assert record["seq_no"] == 3
    assert record["tbc"] is True
    assert record["component_name"] == "Meter"
    assert record["component_instance"] == "main"
    assert record["variable_name"] == "Energy"
    assert record["variable_instance"] == "A"
    assert record["monitoring_id"] == 202
    assert record["severity"] == 3
    assert record["monitor_type"] == "LowerThreshold"
    assert record["threshold"] == "10"
    assert record["is_transaction"] is False
    assert record["evse_id"] == 4
    assert record["connector_id"] == "2"
    assert record["generated_at"] == datetime(2024, 2, 2, 10, 0, tzinfo=dt_timezone.utc)


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_notify_customer_information_persists_chunks():
    charger = await database_sync_to_async(Charger.objects.create)(charger_id="INFO-1")
    existing = await database_sync_to_async(CustomerInformationRequest.objects.create)(
        charger=charger, request_id=7
    )
    consumer = consumers.CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = "INFO-1"
    consumer.charger_id = charger.charger_id
    consumer.charger = charger
    consumer.aggregate_charger = None

    payload = {"requestId": 7, "data": "chunk-data", "tbc": False}
    result = await consumer._handle_notify_customer_information_action(
        payload, "msg-7", "", ""
    )

    assert result == {}
    request = await database_sync_to_async(CustomerInformationRequest.objects.get)(
        pk=existing.pk
    )
    assert request.last_notified_at is not None
    assert request.completed_at is not None
    chunk = await database_sync_to_async(CustomerInformationChunk.objects.get)(
        charger=charger, request_id=7
    )
    assert chunk.request_id == 7
    assert chunk.data == "chunk-data"
    assert chunk.request_id == request.request_id


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_notify_customer_information_routes_to_customer_care_workflow():
    charger = await database_sync_to_async(Charger.objects.create)(charger_id="INFO-ROUTE")
    consumer = consumers.CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = "INFO-ROUTE"
    consumer.charger_id = charger.charger_id
    consumer.charger = charger
    consumer.aggregate_charger = None

    payload = {"requestId": 11, "data": "acknowledged", "tbc": True}
    result = await consumer._handle_notify_customer_information_action(
        payload, "msg-11", "", ""
    )

    assert result == {}
    entries = list(store.logs["charger"].get(consumer.store_key, []))
    assert any("NotifyCustomerInformation" in entry for entry in entries)
    transition = await database_sync_to_async(Transition.objects.get)(
        workflow="customer-care.customer-information",
        identifier="INFO-ROUTE:11",
    )
    assert transition.from_state == "pending"
    assert transition.to_state == "partial"


@pytest.mark.anyio
async def test_notify_customer_information_rejects_non_dict_payload():
    consumer = consumers.CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = "INFO-BAD"

    result = await consumer._handle_notify_customer_information_action([], "msg-bad", "", "")

    assert result == {}
    entries = list(store.logs["charger"].get(consumer.store_key, []))
    assert any("invalid payload" in entry for entry in entries)


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_notify_display_messages_persists_messages():
    charger = await database_sync_to_async(Charger.objects.create)(charger_id="DISP-1")
    consumer = consumers.CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = "DISP-1"
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
    result = await consumer._handle_notify_display_messages_action(
        payload, "msg-9", "", ""
    )

    assert result == {}
    notification = await database_sync_to_async(
        DisplayMessageNotification.objects.get
    )(charger=charger, request_id=9)
    assert notification.completed_at is not None
    message = await database_sync_to_async(DisplayMessage.objects.get)(
        charger=charger, message_id=101
    )
    assert message.notification_id == notification.pk
    assert message.content == "Hello"
    assert message.language == "en"
    assert message.component_name == "Display"
    assert message.variable_name == "Content"


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_notify_display_messages_updates_compliance_report():
    charger = await database_sync_to_async(Charger.objects.create)(charger_id="DISP-2")
    consumer = consumers.CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = "DISP-2"
    consumer.charger_id = charger.charger_id
    consumer.charger = charger
    consumer.aggregate_charger = None

    payload = {
        "requestId": 10,
        "tbc": True,
        "messageInfo": [
            {
                "messageId": 202,
                "priority": "Low",
                "state": "Displayed",
                "message": {"text": "Promo", "language": "es"},
            }
        ],
    }

    await consumer._handle_notify_display_messages_action(payload, "msg-10", "", "")

    reports = store.display_message_compliance.get(charger.charger_id)
    assert reports is not None
    assert reports[0]["request_id"] == 10
    assert reports[0]["tbc"] is True
    assert reports[0]["messages"][0]["message_id"] == 202
    assert reports[0]["messages"][0]["state"] == "Displayed"


@pytest.mark.anyio
async def test_request_start_transaction_result_tracks_status():
    consumer = consumers.CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = "CP-REQ"
    consumer.charger_id = "CP-REQ"
    store.register_transaction_request(
        "msg-req-1",
        {
            "action": "RequestStartTransaction",
            "charger_id": "CP-REQ",
            "connector_id": 1,
        },
    )

    await call_result_handlers.handle_request_start_transaction_result(
        consumer,
        "msg-req-1",
        {"action": "RequestStartTransaction"},
        {"status": "Accepted", "transactionId": "TX-REQ"},
        "CP-REQ",
    )

    assert store.transaction_requests["msg-req-1"]["status"] == "accepted"
    assert store.transaction_requests["msg-req-1"]["transaction_id"] == "TX-REQ"


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_transaction_event_updates_request_status(monkeypatch):
    charger = await database_sync_to_async(Charger.objects.create)(charger_id="CP-TRX")
    consumer = consumers.CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = store.identity_key(charger.charger_id, 1)
    consumer.charger_id = charger.charger_id
    consumer.charger = charger
    consumer.aggregate_charger = None

    async def fake_assign(connector):
        consumer.connector_value = connector

    consumer._assign_connector = AsyncMock(side_effect=fake_assign)
    consumer._start_consumption_updates = AsyncMock()
    consumer._process_meter_value_entries = AsyncMock()
    consumer._record_rfid_attempt = AsyncMock()
    consumer._update_consumption_message = AsyncMock()
    consumer._cancel_consumption_message = AsyncMock()
    consumer._consumption_message_uuid = None

    store.register_transaction_request(
        "msg-req-2",
        {
            "action": "RequestStartTransaction",
            "charger_id": charger.charger_id,
            "connector_id": 1,
            "status": "accepted",
        },
    )

    payload = {
        "eventType": "Started",
        "timestamp": "2024-01-01T00:00:00Z",
        "evse": {"id": 1},
        "transactionInfo": {"transactionId": "TX-201"},
    }

    await consumer._handle_transaction_event_action(payload, "msg-evt-1", "", "")

    assert store.transaction_requests["msg-req-2"]["status"] == "started"
    assert store.transaction_requests["msg-req-2"]["transaction_id"] == "TX-201"

    payload["eventType"] = "Ended"
    await consumer._handle_transaction_event_action(payload, "msg-evt-2", "", "")

    assert store.transaction_requests["msg-req-2"]["status"] == "completed"


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_transaction_event_started_notifies_and_persists():
    charger = await database_sync_to_async(Charger.objects.create)(charger_id="CP-TE-1")
    consumer = consumers.CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = store.identity_key(charger.charger_id, 1)
    consumer.charger_id = charger.charger_id
    consumer.charger = charger
    consumer.aggregate_charger = None

    async def fake_assign(connector):
        consumer.connector_value = connector

    consumer._assign_connector = AsyncMock(side_effect=fake_assign)
    consumer._start_consumption_updates = AsyncMock()
    consumer._process_meter_value_entries = AsyncMock()
    consumer._record_rfid_attempt = AsyncMock()

    payload = {
        "eventType": "Started",
        "timestamp": "2024-01-02T00:00:00Z",
        "evse": {"id": 1, "connectorId": 1},
        "transactionInfo": {"transactionId": "TX-TE-1", "meterStart": 5},
    }

    await consumer._handle_transaction_event_action(payload, "msg-evt-start", "", "")

    tx_obj = await database_sync_to_async(Transaction.objects.get)(charger=charger)
    assert tx_obj.ocpp_transaction_id == "TX-TE-1"
    assert tx_obj.meter_start == 5
    assert tx_obj.start_time == parse_datetime("2024-01-02T00:00:00Z")

    assert store.transaction_events
    event = store.transaction_events[-1]
    assert event["event_type"] == "started"
    assert event["transaction_pk"] == tx_obj.pk
    assert event["ocpp_transaction_id"] == "TX-TE-1"
    assert event["connector_id"] == "1"


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_transaction_event_updated_notifies_existing_transaction():
    charger = await database_sync_to_async(Charger.objects.create)(charger_id="CP-TE-2")
    consumer = consumers.CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = store.identity_key(charger.charger_id, 1)
    consumer.charger_id = charger.charger_id
    consumer.charger = charger
    consumer.aggregate_charger = None

    async def fake_assign(connector):
        consumer.connector_value = connector

    consumer._assign_connector = AsyncMock(side_effect=fake_assign)
    consumer._process_meter_value_entries = AsyncMock()

    now = timezone.now()
    tx_obj = await database_sync_to_async(Transaction.objects.create)(
        charger=charger,
        connector_id=1,
        ocpp_transaction_id="TX-TE-2",
        start_time=now,
        received_start_time=now,
    )
    store.transactions[consumer.store_key] = tx_obj

    payload = {
        "eventType": "Updated",
        "timestamp": "2024-01-03T00:00:00Z",
        "evse": {"id": 1},
        "transactionInfo": {"transactionId": "TX-TE-2"},
    }

    await consumer._handle_transaction_event_action(payload, "msg-evt-update", "", "")

    refreshed = await database_sync_to_async(Transaction.objects.get)(pk=tx_obj.pk)
    assert refreshed.ocpp_transaction_id == "TX-TE-2"

    assert store.transaction_events
    event = store.transaction_events[-1]
    assert event["event_type"] == "updated"
    assert event["transaction_pk"] == tx_obj.pk
    assert event["ocpp_transaction_id"] == "TX-TE-2"
    assert event["connector_id"] == "1"


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_transaction_event_ended_updates_and_notifies():
    charger = await database_sync_to_async(Charger.objects.create)(charger_id="CP-TE-3")
    consumer = consumers.CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = store.identity_key(charger.charger_id, 1)
    consumer.charger_id = charger.charger_id
    consumer.charger = charger
    consumer.aggregate_charger = None

    async def fake_assign(connector):
        consumer.connector_value = connector

    consumer._assign_connector = AsyncMock(side_effect=fake_assign)
    consumer._process_meter_value_entries = AsyncMock()
    consumer._update_consumption_message = AsyncMock()
    consumer._cancel_consumption_message = AsyncMock()
    consumer._consumption_message_uuid = None

    start_ts = timezone.now()
    tx_obj = await database_sync_to_async(Transaction.objects.create)(
        charger=charger,
        connector_id=1,
        ocpp_transaction_id="TX-TE-3",
        start_time=start_ts,
        received_start_time=start_ts,
        meter_start=10,
    )
    store.transactions[consumer.store_key] = tx_obj

    payload = {
        "eventType": "Ended",
        "timestamp": "2024-01-04T00:00:00Z",
        "evse": {"id": 1},
        "transactionInfo": {"transactionId": "TX-TE-3", "meterStop": 50},
    }

    await consumer._handle_transaction_event_action(payload, "msg-evt-end", "", "")

    refreshed = await database_sync_to_async(Transaction.objects.get)(pk=tx_obj.pk)
    assert refreshed.meter_stop == 50
    assert refreshed.stop_time == parse_datetime("2024-01-04T00:00:00Z")
    assert store.transactions.get(consumer.store_key) is None

    assert store.transaction_events
    event = store.transaction_events[-1]
    assert event["event_type"] == "ended"
    assert event["transaction_pk"] == tx_obj.pk
    assert event["meter_stop"] == 50
    assert event["connector_id"] == "1"


@pytest.mark.anyio
async def test_notify_ev_charging_needs_records_requirements(monkeypatch):
    consumer = consumers.CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = store.identity_key("NEEDS-1", 1)
    consumer.charger_id = "NEEDS-1"
    consumer.connector_value = 1

    recorded: list[dict[str, object]] = []

    def _record(*args, **kwargs):
        if args:
            kwargs["charger_id"] = args[0]
        if "connector_id" in kwargs:
            kwargs["connector_id"] = store.connector_slug(kwargs["connector_id"])
        recorded.append(kwargs)

    monkeypatch.setattr(store, "record_ev_charging_needs", _record)

    payload = {
        "evseId": 2,
        "chargingNeeds": {
            "acChargingParameters": {"energyAmount": 12000},
            "departureTime": "2024-01-01T01:30:00Z",
        },
    }

    result = await consumer._handle_notify_ev_charging_needs_action(
        payload, "needs-1", "", ""
    )

    assert result == {}
    assert recorded
    entry = recorded[0]
    assert entry["charger_id"] == "NEEDS-1"
    assert entry["connector_id"] == "1"
    assert entry["evse_id"] == 2
    assert entry["requested_energy"] == 12000
    assert entry["departure_time"].isoformat().startswith("2024-01-01T01:30:00")
    assert entry["charging_needs"]["acChargingParameters"]["energyAmount"] == 12000


@pytest.mark.anyio
async def test_notify_ev_charging_needs_requires_fields(monkeypatch):
    consumer = consumers.CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = "NEEDS-2"
    consumer.charger_id = "NEEDS-2"

    called = False

    def _record(*args, **kwargs):  # pragma: no cover - test guard
        nonlocal called
        called = True

    monkeypatch.setattr(store, "record_ev_charging_needs", _record)

    payload = {"chargingNeeds": {"acChargingParameters": {"energyAmount": 5000}}}

    result = await consumer._handle_notify_ev_charging_needs_action(
        payload, "needs-2", "", ""
    )

    assert result == {}
    assert called is False


@pytest.mark.anyio
async def test_notify_ev_charging_schedule_records_schedule(monkeypatch):
    consumer = consumers.CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = store.identity_key("SCHED-1", 1)
    consumer.charger_id = "SCHED-1"
    consumer.connector_value = 1

    recorded: list[dict[str, object]] = []
    forwarded: list[dict[str, object]] = []

    def _record(*args, **kwargs):
        if args:
            kwargs["charger_id"] = args[0]
        if "connector_id" in kwargs:
            kwargs["connector_id"] = store.connector_slug(kwargs["connector_id"])
        recorded.append(kwargs)

    monkeypatch.setattr(store, "record_ev_charging_schedule", _record)
    monkeypatch.setattr(
        store, "forward_ev_charging_schedule", lambda payload: forwarded.append(payload)
    )

    payload = {
        "timebase": "2024-01-01T00:00:00Z",
        "evseId": 2,
        "chargingSchedule": {
            "id": "9",
            "duration": "600",
            "chargingRateUnit": "W",
            "startSchedule": "2024-01-01T00:05:00Z",
            "chargingSchedulePeriod": [
                {"startPeriod": 0, "limit": 32, "numberPhases": 3},
                {"startPeriod": 300, "limit": "16.5"},
            ],
        },
    }

    result = await consumer._handle_notify_ev_charging_schedule_action(
        payload, "sched-1", "", ""
    )

    assert result == {}
    assert recorded
    entry = recorded[0]
    assert entry["charger_id"] == "SCHED-1"
    assert entry["connector_id"] == "1"
    assert entry["evse_id"] == 2
    assert entry["timebase"].isoformat().startswith("2024-01-01T00:00:00")
    schedule = entry["charging_schedule"]
    assert schedule["id"] == 9
    assert schedule["duration_seconds"] == 600
    assert schedule["charging_rate_unit"] == "W"
    assert schedule["start_schedule"].isoformat().startswith("2024-01-01T00:05:00")
    assert len(schedule["periods"]) == 2
    assert schedule["periods"][0]["limit"] == 32.0
    assert schedule["periods"][0]["number_phases"] == 3
    assert schedule["periods"][1]["limit"] == 16.5
    assert forwarded
    assert forwarded[0]["evse_id"] == 2


@pytest.mark.anyio
async def test_notify_ev_charging_schedule_requires_fields(monkeypatch):
    consumer = consumers.CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = "SCHED-2"
    consumer.charger_id = "SCHED-2"

    recorded = False
    forwarded = False

    def _record(*_args, **_kwargs):  # pragma: no cover - guard
        nonlocal recorded
        recorded = True

    def _forward(*_args, **_kwargs):  # pragma: no cover - guard
        nonlocal forwarded
        forwarded = True

    monkeypatch.setattr(store, "record_ev_charging_schedule", _record)
    monkeypatch.setattr(store, "forward_ev_charging_schedule", _forward)

    result = await consumer._handle_notify_ev_charging_schedule_action(
        {"timebase": "2024-01-01T00:00:00Z"}, "sched-2", "", ""
    )

    assert result == {}
    assert recorded is False
    assert forwarded is False


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_report_charging_profiles_matches_local_state(monkeypatch):
    charger = await database_sync_to_async(Charger.objects.create)(
        charger_id="RCP-1", connector_id=1
    )
    profile = await database_sync_to_async(ChargingProfile.objects.create)(
        charger=charger,
        connector_id=1,
        charging_profile_id=9,
        stack_level=1,
        purpose=ChargingProfile.Purpose.TX_DEFAULT_PROFILE,
        kind=ChargingProfile.Kind.ABSOLUTE,
    )
    await database_sync_to_async(ChargingSchedule.objects.create)(
        profile=profile,
        charging_rate_unit=ChargingProfile.RateUnit.AMP,
        charging_schedule_periods=[{"start_period": 0, "limit": 32}],
    )

    consumer = consumers.CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = store.identity_key(charger.charger_id, charger.connector_id)
    consumer.charger_id = charger.charger_id
    consumer.charger = charger
    consumer.aggregate_charger = None
    consumer.connector_value = charger.connector_id
    consumer._log_ocpp201_notification = lambda *args, **kwargs: None

    logs: list[str] = []
    monkeypatch.setattr(store, "add_log", lambda _cid, entry, log_type="charger": logs.append(entry))

    payload = {
        "requestId": 7,
        "evseId": 1,
        "chargingProfile": profile.as_cs_charging_profile(),
        "tbc": False,
    }

    result = await consumer._handle_report_charging_profiles_action(
        payload, "msg-rcp-1", "", ""
    )

    assert result == {}
    assert logs == []


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_report_charging_profiles_flags_mismatch(monkeypatch):
    charger = await database_sync_to_async(Charger.objects.create)(
        charger_id="RCP-2", connector_id=1
    )
    profile = await database_sync_to_async(ChargingProfile.objects.create)(
        charger=charger,
        connector_id=1,
        charging_profile_id=5,
        stack_level=2,
        purpose=ChargingProfile.Purpose.TX_DEFAULT_PROFILE,
        kind=ChargingProfile.Kind.ABSOLUTE,
    )
    await database_sync_to_async(ChargingSchedule.objects.create)(
        profile=profile,
        charging_rate_unit=ChargingProfile.RateUnit.AMP,
        charging_schedule_periods=[{"start_period": 0, "limit": 16}],
    )

    consumer = consumers.CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = store.identity_key(charger.charger_id, charger.connector_id)
    consumer.charger_id = charger.charger_id
    consumer.charger = charger
    consumer.aggregate_charger = None
    consumer.connector_value = charger.connector_id
    consumer._log_ocpp201_notification = lambda *args, **kwargs: None

    logs: list[str] = []
    monkeypatch.setattr(store, "add_log", lambda _cid, entry, log_type="charger": logs.append(entry))

    payload_profile = profile.as_cs_charging_profile()
    payload_profile["stackLevel"] = 3

    payload = {
        "requestId": 11,
        "evseId": 1,
        "chargingProfile": payload_profile,
        "tbc": False,
    }

    result = await consumer._handle_report_charging_profiles_action(
        payload, "msg-rcp-2", "", ""
    )

    assert result == {}
    assert any("stack level expected" in entry for entry in logs)


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_report_charging_profiles_flags_missing_entries(monkeypatch):
    charger = await database_sync_to_async(Charger.objects.create)(
        charger_id="RCP-3", connector_id=1
    )
    profile_one = await database_sync_to_async(ChargingProfile.objects.create)(
        charger=charger,
        connector_id=1,
        charging_profile_id=2,
        stack_level=1,
        purpose=ChargingProfile.Purpose.TX_DEFAULT_PROFILE,
        kind=ChargingProfile.Kind.ABSOLUTE,
    )
    await database_sync_to_async(ChargingSchedule.objects.create)(
        profile=profile_one,
        charging_rate_unit=ChargingProfile.RateUnit.AMP,
        charging_schedule_periods=[{"start_period": 0, "limit": 10}],
    )

    profile_two = await database_sync_to_async(ChargingProfile.objects.create)(
        charger=charger,
        connector_id=1,
        charging_profile_id=3,
        stack_level=1,
        purpose=ChargingProfile.Purpose.TX_DEFAULT_PROFILE,
        kind=ChargingProfile.Kind.ABSOLUTE,
    )
    await database_sync_to_async(ChargingSchedule.objects.create)(
        profile=profile_two,
        charging_rate_unit=ChargingProfile.RateUnit.AMP,
        charging_schedule_periods=[{"start_period": 0, "limit": 20}],
    )

    consumer = consumers.CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = store.identity_key(charger.charger_id, charger.connector_id)
    consumer.charger_id = charger.charger_id
    consumer.charger = charger
    consumer.aggregate_charger = None
    consumer.connector_value = charger.connector_id
    consumer._log_ocpp201_notification = lambda *args, **kwargs: None

    logs: list[str] = []
    monkeypatch.setattr(store, "add_log", lambda _cid, entry, log_type="charger": logs.append(entry))

    payload = {
        "requestId": 15,
        "evseId": 1,
        "chargingProfile": profile_one.as_cs_charging_profile(),
        "tbc": False,
    }

    result = await consumer._handle_report_charging_profiles_action(
        payload, "msg-rcp-3", "", ""
    )

    assert result == {}
    assert any("ReportChargingProfiles missing" in entry for entry in logs)
    assert any("3" in entry for entry in logs)


@pytest.mark.anyio
async def test_notify_event_forwards_observability_payload(monkeypatch):
    consumer = consumers.CSMSConsumer(scope={}, receive=None, send=None)
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
    assert event["event_type"] == "Alert"
    assert event["trigger"] == "Delta"
    assert event["actual_value"] == "85C"
    assert event["severity"] == 1
    assert event["cause"] == "Overheat"
    assert event["tech_code"] == "TMP"
    assert event["tech_info"] == "Sensor drift"
    assert event["cleared"] is False
    assert event["transaction_id"] == "TX-9"
    assert event["variable_monitoring_id"] == 3
    assert event["component_name"] == "Temperature"
    assert event["component_instance"] == "core"
    assert event["variable_name"] == "Temp"
    assert event["variable_instance"] == "A"
    assert event["seq_no"] == 9
    assert event["tbc"] is True
    assert event["generated_at"].isoformat().startswith("2024-01-01T00:00:00")
    assert event["event_timestamp"].isoformat().startswith("2024-01-01T00:00:05")


@pytest.mark.anyio
async def test_notify_event_requires_event_data(monkeypatch):
    consumer = consumers.CSMSConsumer(scope={}, receive=None, send=None)
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


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "status,confirmed",
    [("Accepted", True), ("Cancelled", False), ("Expired", False)],
)
async def test_reservation_status_update_persists_and_notifies(status, confirmed):
    from apps.ocpp.models import Charger as ChargerModel

    location = await database_sync_to_async(Location.objects.create)(name="Depot")
    charger = await database_sync_to_async(ChargerModel.objects.create)(
        charger_id="CP-RES-1", connector_id=1, location=location
    )
    reservation = await database_sync_to_async(CPReservation.objects.create)(
        location=location,
        connector=charger,
        start_time=timezone.now(),
        duration_minutes=30,
    )

    consumer = consumers.CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = store.identity_key(charger.charger_id, charger.connector_id)
    consumer.charger = charger
    consumer.aggregate_charger = None
    consumer.connector_value = charger.connector_id

    payload = {
        "reservationId": reservation.pk,
        "reservationUpdateStatus": status,
    }

    result = await consumer._handle_reservation_status_update_action(
        payload, "resv-msg-1", "", ""
    )

    assert result == {}
    updated = await database_sync_to_async(CPReservation.objects.get)(pk=reservation.pk)
    assert updated.evcs_status == status
    assert updated.evcs_confirmed is confirmed
    assert bool(updated.evcs_confirmed_at) == confirmed

    assert store.connector_release_notifications
    notification = store.connector_release_notifications[-1]
    assert notification == {
        "charger_id": charger.charger_id,
        "connector_id": charger.connector_id,
        "reservation_id": reservation.pk,
        "status": status,
    }


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_reservation_status_update_ignored_for_other_connector():
    from apps.ocpp.models import Charger as ChargerModel

    location = await database_sync_to_async(Location.objects.create)(name="Depot")
    primary = await database_sync_to_async(ChargerModel.objects.create)(
        charger_id="CP-RES-PRIMARY", connector_id=1, location=location
    )
    other = await database_sync_to_async(ChargerModel.objects.create)(
        charger_id="CP-RES-OTHER", connector_id=1, location=location
    )
    reservation = await database_sync_to_async(CPReservation.objects.create)(
        location=location,
        connector=primary,
        start_time=timezone.now(),
        duration_minutes=30,
    )

    consumer = consumers.CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = store.identity_key(other.charger_id, other.connector_id)
    consumer.charger = other
    consumer.aggregate_charger = None
    consumer.connector_value = other.connector_id

    payload = {
        "reservationId": reservation.pk,
        "reservationUpdateStatus": "Accepted",
    }

    result = await consumer._handle_reservation_status_update_action(
        payload, "resv-msg-2", "", ""
    )

    assert result == {}
    updated = await database_sync_to_async(CPReservation.objects.get)(pk=reservation.pk)
    assert updated.evcs_status == ""
    assert updated.evcs_confirmed is False
    assert updated.evcs_confirmed_at is None
    assert not store.connector_release_notifications
