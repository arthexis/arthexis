from unittest.mock import AsyncMock
import base64

import anyio
from functools import partial

import pytest
from channels.db import database_sync_to_async
from django.utils import timezone

from apps.ocpp import consumers, store, call_result_handlers
from apps.ocpp.models import (
    Charger,
    CertificateRequest,
    CertificateStatusCheck,
    CostUpdate,
    Transaction,
    Variable,
    MonitoringRule,
    MonitoringReport,
    CustomerInformationRequest,
    CustomerInformationChunk,
    DisplayMessageNotification,
    DisplayMessage,
    ClearedChargingLimitEvent,
)
from apps.protocols.models import ProtocolCall as ProtocolCallModel


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
    store.clear_credential_requests()
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


@pytest.fixture
def anyio_backend():
    return "asyncio"


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
async def test_notify_report_logs_payload():
    consumer = consumers.CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = "CP-201"

    result = await consumer._handle_notify_report_action({"count": 2}, "msg-2", "", "")

    assert result == {}
    entries = list(store.logs["charger"][consumer.store_key])
    assert any("NotifyReport" in entry for entry in entries)
    assert any("count" in entry for entry in entries)


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
async def test_get_15118_ev_certificate_persists_request():
    charger = await database_sync_to_async(Charger.objects.create)(charger_id="CERT-1")
    consumer = consumers.CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = "CERT-1"
    consumer.charger = charger
    consumer.aggregate_charger = None

    store.clear_credential_requests()
    payload = {
        "certificateType": "V2G",
        "iso15118SchemaVersion": "2.0.1",
        "exiRequest": base64.b64encode(b"CSRDATA").decode(),
    }
    result = await consumer._handle_get_15118_ev_certificate_action(
        payload, "msg-1", "", ""
    )

    assert result["status"] == "Pending"
    request = await database_sync_to_async(CertificateRequest.objects.get)(charger=charger)
    assert request.action == CertificateRequest.ACTION_15118
    assert request.csr == payload["exiRequest"]
    assert request.status == CertificateRequest.STATUS_PENDING
    assert request.response_payload["status"] == "Pending"
    queued = list(store.iter_credential_requests("CERT-1"))
    assert len(queued) == 1
    assert queued[0]["schema_version"] == "2.0.1"


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_get_certificate_status_persists_check():
    charger = await database_sync_to_async(Charger.objects.create)(charger_id="CERT-2")
    consumer = consumers.CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = "CERT-2"
    consumer.charger = charger
    consumer.aggregate_charger = None

    payload = {"certificateHashData": {"hashAlgorithm": "SHA256"}}
    result = await consumer._handle_get_certificate_status_action(
        payload, "msg-2", "", ""
    )

    assert result["status"] == "Rejected"
    status_check = await database_sync_to_async(CertificateStatusCheck.objects.get)(
        charger=charger
    )
    assert status_check.status == CertificateStatusCheck.STATUS_REJECTED
    assert status_check.certificate_hash_data["hashAlgorithm"] == "SHA256"


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_sign_certificate_persists_request():
    charger = await database_sync_to_async(Charger.objects.create)(charger_id="CERT-3")
    consumer = consumers.CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = "CERT-3"
    consumer.charger = charger
    consumer.aggregate_charger = None

    payload = {"csr": "CSR-123", "certificateType": "V2G"}
    result = await consumer._handle_sign_certificate_action(
        payload, "msg-3", "", ""
    )

    assert result["status"] == "Rejected"
    request = await database_sync_to_async(CertificateRequest.objects.get)(charger=charger)
    assert request.action == CertificateRequest.ACTION_SIGN
    assert request.csr == "CSR-123"


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_get_15118_ev_certificate_validation_error():
    charger = await database_sync_to_async(Charger.objects.create)(charger_id="CERT-4")
    consumer = consumers.CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = "CERT-4"
    consumer.charger = charger
    consumer.aggregate_charger = None

    store.clear_credential_requests()
    payload = {"certificateType": "V2G", "exiRequest": "!!!", "iso15118SchemaVersion": ""}
    result = await consumer._handle_get_15118_ev_certificate_action(
        payload, "msg-4", "", ""
    )

    assert result["status"] == "Rejected"
    assert "statusInfo" in result
    queued = list(store.iter_credential_requests("CERT-4"))
    assert queued == []
    request = await database_sync_to_async(CertificateRequest.objects.get)(charger=charger)
    assert request.status == CertificateRequest.STATUS_REJECTED
    assert "exiRequest must be base64 encoded" in request.status_info


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
