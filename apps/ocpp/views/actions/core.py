import json
import uuid

from django.http import JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from asgiref.sync import async_to_sync

from apps.protocols.decorators import protocol_call
from apps.protocols.models import ProtocolCall as ProtocolCallModel

from ... import store
from ...models import Charger, ChargerLogRequest, CustomerInformationRequest, DataTransferMessage
from .common import (
    CALL_EXPECTED_STATUSES,
    ActionCall,
    ActionContext,
    _get_or_create_charger,
)


@protocol_call("ocpp16", ProtocolCallModel.CSMS_TO_CP, "RemoteStopTransaction")
def _handle_remote_stop(context: ActionContext, _data: dict) -> JsonResponse | ActionCall:
    tx_obj = store.get_transaction(context.cid, context.connector_value)
    if not tx_obj:
        return JsonResponse({"detail": "no transaction"}, status=404)
    message_id = uuid.uuid4().hex
    ocpp_version = str(getattr(context.ws, "ocpp_version", "") or "")
    ocpp_action = "RemoteStopTransaction"
    payload: dict[str, object] = {"transactionId": tx_obj.pk}
    if ocpp_version.startswith("ocpp2.0"):
        tx_identifier = tx_obj.ocpp_transaction_id or str(tx_obj.pk)
        payload = {"transactionId": str(tx_identifier)}
        ocpp_action = "RequestStopTransaction"
    expected_statuses = CALL_EXPECTED_STATUSES.get(ocpp_action)
    msg = json.dumps([2, message_id, ocpp_action, payload])
    async_to_sync(context.ws.send)(msg)
    store.register_pending_call(
        message_id,
        {
            "action": ocpp_action,
            "charger_id": context.cid,
            "connector_id": context.connector_value,
            "log_key": context.log_key,
            "transaction_id": tx_obj.pk,
            "requested_at": timezone.now(),
        },
    )
    return ActionCall(
        msg=msg,
        message_id=message_id,
        ocpp_action=ocpp_action,
        expected_statuses=expected_statuses,
    )


@protocol_call("ocpp16", ProtocolCallModel.CSMS_TO_CP, "RemoteStartTransaction")
def _handle_remote_start(context: ActionContext, data: dict) -> JsonResponse | ActionCall:
    id_tag = data.get("idTag")
    if not isinstance(id_tag, str) or not id_tag.strip():
        return JsonResponse({"detail": "idTag required"}, status=400)
    id_tag = id_tag.strip()
    ocpp_version = str(getattr(context.ws, "ocpp_version", "") or "")
    payload: dict[str, object]
    ocpp_action = "RemoteStartTransaction"
    if ocpp_version.startswith("ocpp2.0"):
        remote_start_id = data.get("remoteStartId")
        try:
            remote_start_id_int = int(remote_start_id)
        except (TypeError, ValueError):
            remote_start_id_int = int(uuid.uuid4().int % 1_000_000_000)
        payload = {
            "idToken": {"idToken": id_tag, "type": "Central"},
            "remoteStartId": remote_start_id_int,
        }
        ocpp_action = "RequestStartTransaction"
    else:
        payload = {"idTag": id_tag}
    connector_id = data.get("connectorId")
    if connector_id in ("", None):
        connector_id = None
    if connector_id is None and context.connector_value is not None:
        connector_id = context.connector_value
    if connector_id is not None:
        try:
            connector_payload = int(connector_id)
        except (TypeError, ValueError):
            connector_payload = connector_id
        if ocpp_action == "RequestStartTransaction":
            payload["evseId"] = connector_payload
        else:
            payload["connectorId"] = connector_payload
    if "chargingProfile" in data and data["chargingProfile"] is not None:
        payload["chargingProfile"] = data["chargingProfile"]
    message_id = uuid.uuid4().hex
    expected_statuses = CALL_EXPECTED_STATUSES.get(ocpp_action)
    msg = json.dumps([2, message_id, ocpp_action, payload])
    async_to_sync(context.ws.send)(msg)
    store.register_pending_call(
        message_id,
        {
            "action": ocpp_action,
            "charger_id": context.cid,
            "connector_id": context.connector_value,
            "log_key": context.log_key,
            "id_tag": id_tag,
            "requested_at": timezone.now(),
        },
    )
    return ActionCall(
        msg=msg,
        message_id=message_id,
        ocpp_action=ocpp_action,
        expected_statuses=expected_statuses,
    )


@protocol_call("ocpp201", ProtocolCallModel.CSMS_TO_CP, "RequestStartTransaction")
def _handle_request_start_transaction(
    context: ActionContext, data: dict
) -> JsonResponse | ActionCall:
    raw_id_token = data.get("idToken") or data.get("idTag")
    if not isinstance(raw_id_token, str) or not raw_id_token.strip():
        return JsonResponse({"detail": "idToken required"}, status=400)
    id_token_value = raw_id_token.strip()
    id_token_type = data.get("idTokenType") or data.get("type") or "Central"
    if not isinstance(id_token_type, str) or not id_token_type.strip():
        return JsonResponse({"detail": "idToken type required"}, status=400)
    id_token_type = id_token_type.strip()

    remote_start_value = data.get("remoteStartId")
    if remote_start_value in (None, ""):
        remote_start_id = int(uuid.uuid4().int % 1_000_000_000)
    else:
        try:
            remote_start_id = int(remote_start_value)
        except (TypeError, ValueError):
            return JsonResponse({"detail": "remoteStartId must be an integer"}, status=400)

    evse_value = data.get("evseId")
    if evse_value in (None, ""):
        evse_value = data.get("connectorId")
    if evse_value in (None, ""):
        evse_value = context.connector_value
    evse_payload: int | str | None = None
    if evse_value not in (None, ""):
        try:
            evse_payload = int(evse_value)
        except (TypeError, ValueError):
            evse_payload = evse_value

    payload: dict[str, object] = {
        "idToken": {"idToken": id_token_value, "type": id_token_type},
        "remoteStartId": remote_start_id,
    }
    if evse_payload is not None:
        payload["evseId"] = evse_payload
    if "chargingProfile" in data and data["chargingProfile"] is not None:
        payload["chargingProfile"] = data["chargingProfile"]

    message_id = uuid.uuid4().hex
    ocpp_action = "RequestStartTransaction"
    expected_statuses = CALL_EXPECTED_STATUSES.get(ocpp_action)
    msg = json.dumps([2, message_id, ocpp_action, payload])
    async_to_sync(context.ws.send)(msg)
    requested_at = timezone.now()
    metadata = {
        "action": ocpp_action,
        "charger_id": context.cid,
        "connector_id": evse_payload,
        "log_key": context.log_key,
        "id_token": id_token_value,
        "id_token_type": id_token_type,
        "remote_start_id": remote_start_id,
        "requested_at": requested_at,
    }
    store.register_pending_call(message_id, metadata)
    store.register_transaction_request(message_id, metadata)
    return ActionCall(
        msg=msg,
        message_id=message_id,
        ocpp_action=ocpp_action,
        expected_statuses=expected_statuses,
    )


@protocol_call("ocpp201", ProtocolCallModel.CSMS_TO_CP, "RequestStopTransaction")
def _handle_request_stop_transaction(
    context: ActionContext, data: dict
) -> JsonResponse | ActionCall:
    transaction_id = data.get("transactionId") or data.get("transaction_id")
    tx_obj = None
    if transaction_id in (None, ""):
        tx_obj = store.get_transaction(context.cid, context.connector_value)
        if not tx_obj:
            return JsonResponse({"detail": "transactionId required"}, status=400)
        transaction_id = tx_obj.ocpp_transaction_id or str(tx_obj.pk)
    transaction_text = str(transaction_id).strip()
    if not transaction_text:
        return JsonResponse({"detail": "transactionId required"}, status=400)
    payload = {"transactionId": transaction_text}
    message_id = uuid.uuid4().hex
    ocpp_action = "RequestStopTransaction"
    expected_statuses = CALL_EXPECTED_STATUSES.get(ocpp_action)
    msg = json.dumps([2, message_id, ocpp_action, payload])
    async_to_sync(context.ws.send)(msg)
    connector_id = context.connector_value
    if tx_obj is not None:
        connector_id = getattr(tx_obj, "connector_id", connector_id)
    requested_at = timezone.now()
    metadata = {
        "action": ocpp_action,
        "charger_id": context.cid,
        "connector_id": connector_id,
        "log_key": context.log_key,
        "transaction_id": transaction_text,
        "requested_at": requested_at,
    }
    store.register_pending_call(message_id, metadata)
    store.register_transaction_request(message_id, metadata)
    return ActionCall(
        msg=msg,
        message_id=message_id,
        ocpp_action=ocpp_action,
        expected_statuses=expected_statuses,
    )


@protocol_call("ocpp201", ProtocolCallModel.CSMS_TO_CP, "GetTransactionStatus")
def _handle_get_transaction_status(
    context: ActionContext, data: dict
) -> JsonResponse | ActionCall:
    transaction_id = data.get("transactionId") or data.get("transaction_id")
    payload: dict[str, object] = {}
    if transaction_id not in (None, ""):
        transaction_text = str(transaction_id).strip()
        if not transaction_text:
            return JsonResponse({"detail": "transactionId must not be blank"}, status=400)
        payload["transactionId"] = transaction_text
    message_id = uuid.uuid4().hex
    ocpp_action = "GetTransactionStatus"
    expected_statuses = CALL_EXPECTED_STATUSES.get(ocpp_action)
    msg = json.dumps([2, message_id, ocpp_action, payload])
    async_to_sync(context.ws.send)(msg)
    store.register_pending_call(
        message_id,
        {
            "action": ocpp_action,
            "charger_id": context.cid,
            "connector_id": context.connector_value,
            "log_key": context.log_key,
            "transaction_id": payload.get("transactionId"),
            "requested_at": timezone.now(),
        },
    )
    store.schedule_call_timeout(
        message_id,
        action=ocpp_action,
        log_key=context.log_key,
        message="GetTransactionStatus request timed out",
    )
    return ActionCall(
        msg=msg,
        message_id=message_id,
        ocpp_action=ocpp_action,
        expected_statuses=expected_statuses,
    )


@protocol_call("ocpp16", ProtocolCallModel.CSMS_TO_CP, "GetDiagnostics")
def _handle_get_diagnostics(
    context: ActionContext, data: dict
) -> JsonResponse | ActionCall:
    expires_at = None
    stop_time_raw = data.get("stopTime") or data.get("expiresAt") or data.get("expires_at")
    if stop_time_raw not in (None, ""):
        parsed_stop_time = parse_datetime(str(stop_time_raw))
        if parsed_stop_time is None:
            return JsonResponse({"detail": "invalid stopTime"}, status=400)
        if timezone.is_naive(parsed_stop_time):
            parsed_stop_time = timezone.make_aware(
                parsed_stop_time, timezone.get_current_timezone()
            )
        expires_at = parsed_stop_time

    charger_obj = context.charger
    if charger_obj is None:
        return JsonResponse({"detail": "charger not found"}, status=404)

    bucket = charger_obj.ensure_diagnostics_bucket(expires_at=expires_at)
    upload_path = reverse("ocpp:media-bucket-upload", kwargs={"slug": bucket.slug})
    request_obj = getattr(context, "request", None)
    if request_obj is not None:
        location = request_obj.build_absolute_uri(upload_path)
    else:  # pragma: no cover - fallback for atypical contexts
        location = upload_path
    payload: dict[str, object] = {"location": location}
    if bucket.expires_at:
        payload["stopTime"] = bucket.expires_at.isoformat()

    message_id = uuid.uuid4().hex
    ocpp_action = "GetDiagnostics"
    expected_statuses = CALL_EXPECTED_STATUSES.get(ocpp_action)
    msg = json.dumps([2, message_id, ocpp_action, payload])
    async_to_sync(context.ws.send)(msg)
    log_key = context.log_key
    store.register_pending_call(
        message_id,
        {
            "action": ocpp_action,
            "charger_id": context.cid,
            "connector_id": context.connector_value,
            "log_key": log_key,
            "location": location,
            "requested_at": timezone.now(),
        },
    )
    store.schedule_call_timeout(
        message_id,
        action=ocpp_action,
        log_key=log_key,
        message="GetDiagnostics request timed out",
    )
    return ActionCall(
        msg=msg,
        message_id=message_id,
        ocpp_action=ocpp_action,
        expected_statuses=expected_statuses,
        log_key=log_key,
    )


@protocol_call("ocpp201", ProtocolCallModel.CSMS_TO_CP, "ChangeAvailability")
@protocol_call("ocpp16", ProtocolCallModel.CSMS_TO_CP, "ChangeAvailability")
def _handle_change_availability(context: ActionContext, data: dict) -> JsonResponse | ActionCall:
    availability_type = data.get("type")
    if availability_type not in {"Operative", "Inoperative"}:
        return JsonResponse({"detail": "invalid availability type"}, status=400)
    connector_payload = context.connector_value if context.connector_value is not None else 0
    ocpp_version = str(getattr(context.ws, "ocpp_version", "") or "")
    if "connectorId" in data:
        candidate = data.get("connectorId")
        if candidate not in (None, ""):
            try:
                connector_payload = int(candidate)
            except (TypeError, ValueError):
                connector_payload = candidate
    if "evseId" in data:
        evse_candidate = data.get("evseId")
        if evse_candidate not in (None, ""):
            try:
                connector_payload = int(evse_candidate)
            except (TypeError, ValueError):
                connector_payload = evse_candidate
    message_id = uuid.uuid4().hex
    ocpp_action = "ChangeAvailability"
    expected_statuses = CALL_EXPECTED_STATUSES.get(ocpp_action)
    payload = {"connectorId": connector_payload, "type": availability_type}
    if ocpp_version.startswith("ocpp2.0"):
        payload = {"operationalStatus": availability_type}
        if connector_payload not in (None, ""):
            payload["evseId"] = connector_payload
    msg = json.dumps([2, message_id, "ChangeAvailability", payload])
    async_to_sync(context.ws.send)(msg)
    requested_at = timezone.now()
    store.register_pending_call(
        message_id,
        {
            "action": "ChangeAvailability",
            "charger_id": context.cid,
            "connector_id": context.connector_value,
            "availability_type": availability_type,
            "requested_at": requested_at,
        },
    )
    if context.charger:
        updates = {
            "availability_requested_state": availability_type,
            "availability_requested_at": requested_at,
            "availability_request_status": "",
            "availability_request_status_at": None,
            "availability_request_details": "",
        }
        Charger.objects.filter(pk=context.charger.pk).update(**updates)
        for field, value in updates.items():
            setattr(context.charger, field, value)
    return ActionCall(
        msg=msg,
        message_id=message_id,
        ocpp_action=ocpp_action,
        expected_statuses=expected_statuses,
    )


@protocol_call("ocpp201", ProtocolCallModel.CSMS_TO_CP, "ClearCache")
@protocol_call("ocpp16", ProtocolCallModel.CSMS_TO_CP, "ClearCache")
def _handle_clear_cache(context: ActionContext, _data: dict) -> JsonResponse | ActionCall:
    message_id = uuid.uuid4().hex
    ocpp_action = "ClearCache"
    expected_statuses = CALL_EXPECTED_STATUSES.get(ocpp_action)
    msg = json.dumps([2, message_id, "ClearCache", {}])
    async_to_sync(context.ws.send)(msg)
    store.register_pending_call(
        message_id,
        {
            "action": "ClearCache",
            "charger_id": context.cid,
            "connector_id": context.connector_value,
            "log_key": context.log_key,
            "requested_at": timezone.now(),
        },
    )
    return ActionCall(
        msg=msg,
        message_id=message_id,
        ocpp_action=ocpp_action,
        expected_statuses=expected_statuses,
    )


@protocol_call("ocpp201", ProtocolCallModel.CSMS_TO_CP, "GetLog")
def _handle_get_log(context: ActionContext, data: dict) -> JsonResponse | ActionCall:
    if context.charger is None:
        return JsonResponse({"detail": "charger not found"}, status=404)

    log_type = str(data.get("logType") or data.get("log_type") or "").strip()
    request = ChargerLogRequest.objects.create(
        charger=context.charger,
        log_type=log_type,
        status="Pending",
    )

    message_id = uuid.uuid4().hex
    capture_key = store.start_log_capture(
        context.cid,
        context.connector_value,
        request.request_id,
    )
    request.message_id = message_id
    request.session_key = capture_key
    request.status = "Requested"
    request.save(update_fields=["message_id", "session_key", "status"])

    payload: dict[str, object] = {"requestId": request.request_id}
    if log_type:
        payload["logType"] = log_type
    if "location" in data and data["location"] not in (None, ""):
        payload["location"] = str(data.get("location"))
    elif "remoteLocation" in data and data["remoteLocation"] not in (None, ""):
        payload["remoteLocation"] = str(data.get("remoteLocation"))

    message = json.dumps([2, message_id, "GetLog", payload])
    async_to_sync(context.ws.send)(message)

    log_key = context.log_key
    store.register_pending_call(
        message_id,
        {
            "action": "GetLog",
            "charger_id": context.cid,
            "connector_id": context.connector_value,
            "log_key": log_key,
            "log_request_pk": request.pk,
            "capture_key": capture_key,
            "message_id": message_id,
            "requested_at": timezone.now(),
        },
    )
    store.schedule_call_timeout(
        message_id,
        timeout=10.0,
        action="GetLog",
        log_key=log_key,
        message="GetLog request timed out",
    )

    return ActionCall(
        msg=message,
        message_id=message_id,
        ocpp_action="GetLog",
        expected_statuses=CALL_EXPECTED_STATUSES.get("GetLog"),
        log_key=log_key,
    )


@protocol_call("ocpp201", ProtocolCallModel.CSMS_TO_CP, "UnlockConnector")
@protocol_call("ocpp16", ProtocolCallModel.CSMS_TO_CP, "UnlockConnector")
def _handle_unlock_connector(context: ActionContext, data: dict) -> JsonResponse | ActionCall:
    connector_value = data.get("connectorId")
    if connector_value is None:
        connector_value = context.connector_value
    if connector_value is None:
        return JsonResponse({"detail": "connectorId required"}, status=400)
    try:
        connector_id = int(connector_value)
    except (TypeError, ValueError):
        return JsonResponse({"detail": "connectorId must be an integer"}, status=400)
    if connector_id <= 0:
        return JsonResponse({"detail": "connectorId must be positive"}, status=400)
    payload = {"connectorId": connector_id}
    ocpp_version = str(getattr(context.ws, "ocpp_version", "") or "")
    ocpp_action = "UnlockConnector"
    if ocpp_version.startswith("ocpp2.0"):
        payload = {"evseId": connector_id, "connectorId": 1}
        ocpp_action = "UnlockConnector"
    message_id = uuid.uuid4().hex
    expected_statuses = CALL_EXPECTED_STATUSES.get(ocpp_action)
    msg = json.dumps([2, message_id, ocpp_action, payload])
    async_to_sync(context.ws.send)(msg)
    store.register_pending_call(
        message_id,
        {
            "action": ocpp_action,
            "charger_id": context.cid,
            "connector_id": connector_id,
            "log_key": context.log_key,
            "requested_at": timezone.now(),
        },
    )
    store.schedule_call_timeout(
        message_id,
        action=ocpp_action,
        log_key=context.log_key,
        message="UnlockConnector request timed out",
    )
    return ActionCall(
        msg=msg,
        message_id=message_id,
        ocpp_action=ocpp_action,
        expected_statuses=expected_statuses,
    )


@protocol_call("ocpp201", ProtocolCallModel.CSMS_TO_CP, "DataTransfer")
@protocol_call("ocpp16", ProtocolCallModel.CSMS_TO_CP, "DataTransfer")
def _handle_data_transfer(context: ActionContext, data: dict) -> JsonResponse | ActionCall:
    vendor_id = data.get("vendorId") or data.get("vendor_id")
    if not isinstance(vendor_id, str) or not vendor_id.strip():
        return JsonResponse({"detail": "vendorId required"}, status=400)
    vendor_id = vendor_id.strip()
    message_id = uuid.uuid4().hex
    ocpp_action = "DataTransfer"
    expected_statuses = CALL_EXPECTED_STATUSES.get(ocpp_action)
    payload = {"vendorId": vendor_id}
    message_data = data.get("message") or data.get("data")
    if message_data not in (None, ""):
        payload["message"] = message_data
    msg = json.dumps([2, message_id, ocpp_action, payload])
    async_to_sync(context.ws.send)(msg)
    request = DataTransferMessage.objects.create(
        charger=context.charger,
        message_id=message_id,
        vendor_id=vendor_id,
        payload=payload,
    )
    store.register_pending_call(
        message_id,
        {
            "action": ocpp_action,
            "charger_id": context.cid,
            "connector_id": context.connector_value,
            "log_key": context.log_key,
            "requested_at": timezone.now(),
            "data_transfer_pk": request.pk,
        },
    )
    store.schedule_call_timeout(
        message_id,
        action=ocpp_action,
        log_key=context.log_key,
        message="DataTransfer request timed out",
    )
    return ActionCall(
        msg=msg,
        message_id=message_id,
        ocpp_action=ocpp_action,
        expected_statuses=expected_statuses,
    )


@protocol_call("ocpp201", ProtocolCallModel.CSMS_TO_CP, "Reset")
@protocol_call("ocpp16", ProtocolCallModel.CSMS_TO_CP, "Reset")
def _handle_reset(context: ActionContext, _data: dict) -> JsonResponse | ActionCall:
    message_id = uuid.uuid4().hex
    ocpp_action = "Reset"
    expected_statuses = CALL_EXPECTED_STATUSES.get(ocpp_action)
    msg = json.dumps([2, message_id, "Reset", {}])
    async_to_sync(context.ws.send)(msg)
    store.register_pending_call(
        message_id,
        {
            "action": "Reset",
            "charger_id": context.cid,
            "connector_id": context.connector_value,
            "log_key": context.log_key,
            "requested_at": timezone.now(),
        },
    )
    return ActionCall(
        msg=msg,
        message_id=message_id,
        ocpp_action=ocpp_action,
        expected_statuses=expected_statuses,
    )


@protocol_call("ocpp201", ProtocolCallModel.CSMS_TO_CP, "TriggerMessage")
@protocol_call("ocpp16", ProtocolCallModel.CSMS_TO_CP, "TriggerMessage")
def _handle_trigger_message(context: ActionContext, data: dict) -> JsonResponse | ActionCall:
    trigger_target = data.get("target") or data.get("triggerTarget")
    if not isinstance(trigger_target, str) or not trigger_target.strip():
        return JsonResponse({"detail": "target required"}, status=400)
    trigger_target = trigger_target.strip()
    allowed_targets = {
        "BootNotification",
        "DiagnosticsStatusNotification",
        "FirmwareStatusNotification",
        "Heartbeat",
        "MeterValues",
        "StatusNotification",
    }
    if trigger_target not in allowed_targets:
        return JsonResponse({"detail": "invalid target"}, status=400)
    payload: dict[str, object] = {"requestedMessage": trigger_target}
    trigger_connector = None
    connector_field = data.get("connectorId")
    if connector_field in (None, ""):
        connector_field = data.get("connector")
    if connector_field in (None, "") and context.connector_value is not None:
        connector_field = context.connector_value
    if connector_field not in (None, ""):
        try:
            trigger_connector = int(connector_field)
        except (TypeError, ValueError):
            return JsonResponse({"detail": "connectorId must be an integer"}, status=400)
        if trigger_connector <= 0:
            return JsonResponse({"detail": "connectorId must be positive"}, status=400)
        payload["connectorId"] = trigger_connector
    message_id = uuid.uuid4().hex
    ocpp_action = "TriggerMessage"
    expected_statuses = CALL_EXPECTED_STATUSES.get(ocpp_action)
    msg = json.dumps([2, message_id, "TriggerMessage", payload])
    async_to_sync(context.ws.send)(msg)
    store.register_pending_call(
        message_id,
        {
            "action": "TriggerMessage",
            "charger_id": context.cid,
            "connector_id": context.connector_value,
            "log_key": context.log_key,
            "trigger_target": trigger_target,
            "trigger_connector": trigger_connector,
            "requested_at": timezone.now(),
        },
    )
    return ActionCall(
        msg=msg,
        message_id=message_id,
        ocpp_action=ocpp_action,
        expected_statuses=expected_statuses,
    )


@protocol_call("ocpp201", ProtocolCallModel.CSMS_TO_CP, "SendLocalList")
@protocol_call("ocpp16", ProtocolCallModel.CSMS_TO_CP, "SendLocalList")
def _handle_send_local_list(context: ActionContext, data: dict) -> JsonResponse | ActionCall:
    entries = data.get("localAuthorizationList")
    if entries is None:
        entries = data.get("local_authorization_list")
    if entries is None:
        entries = []
    if not isinstance(entries, list):
        return JsonResponse({"detail": "localAuthorizationList must be a list"}, status=400)
    version_candidate = data.get("listVersion")
    if version_candidate is None:
        version_candidate = data.get("list_version")
    if version_candidate is None:
        list_version = ((context.charger.local_auth_list_version or 0) + 1) if context.charger else 1
    else:
        try:
            list_version = int(version_candidate)
        except (TypeError, ValueError):
            return JsonResponse({"detail": "invalid listVersion"}, status=400)
        if list_version <= 0:
            return JsonResponse({"detail": "invalid listVersion"}, status=400)
    update_type = (
        str(data.get("updateType") or data.get("update_type") or "Full").strip() or "Full"
    )
    payload = {
        "listVersion": list_version,
        "updateType": update_type,
        "localAuthorizationList": entries,
    }
    message_id = uuid.uuid4().hex
    ocpp_action = "SendLocalList"
    expected_statuses = CALL_EXPECTED_STATUSES.get(ocpp_action)
    msg = json.dumps([2, message_id, "SendLocalList", payload])
    async_to_sync(context.ws.send)(msg)
    requested_at = timezone.now()
    store.register_pending_call(
        message_id,
        {
            "action": "SendLocalList",
            "charger_id": context.cid,
            "connector_id": context.connector_value,
            "log_key": context.log_key,
            "list_version": list_version,
            "list_size": len(entries),
            "requested_at": requested_at,
        },
    )
    store.schedule_call_timeout(
        message_id,
        action="SendLocalList",
        log_key=context.log_key,
        message="SendLocalList request timed out",
    )
    return ActionCall(
        msg=msg,
        message_id=message_id,
        ocpp_action=ocpp_action,
        expected_statuses=expected_statuses,
    )


@protocol_call("ocpp201", ProtocolCallModel.CSMS_TO_CP, "GetLocalListVersion")
@protocol_call("ocpp16", ProtocolCallModel.CSMS_TO_CP, "GetLocalListVersion")
def _handle_get_local_list_version(context: ActionContext, _data: dict) -> JsonResponse | ActionCall:
    message_id = uuid.uuid4().hex
    ocpp_action = "GetLocalListVersion"
    expected_statuses = CALL_EXPECTED_STATUSES.get(ocpp_action)
    msg = json.dumps([2, message_id, "GetLocalListVersion", {}])
    async_to_sync(context.ws.send)(msg)
    store.register_pending_call(
        message_id,
        {
            "action": "GetLocalListVersion",
            "charger_id": context.cid,
            "connector_id": context.connector_value,
            "log_key": context.log_key,
            "requested_at": timezone.now(),
        },
    )
    store.schedule_call_timeout(
        message_id,
        action="GetLocalListVersion",
        log_key=context.log_key,
        message="GetLocalListVersion request timed out",
    )
    return ActionCall(
        msg=msg,
        message_id=message_id,
        ocpp_action=ocpp_action,
        expected_statuses=expected_statuses,
    )


def _coerce_bool(value: object, field_name: str) -> tuple[bool | None, str | None]:
    if isinstance(value, bool):
        return value, None
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value), None
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "1"}:
            return True, None
        if lowered in {"false", "no", "0"}:
            return False, None
    return None, f"{field_name} must be a boolean"


@protocol_call("ocpp201", ProtocolCallModel.CSMS_TO_CP, "CustomerInformation")
def _handle_customer_information(
    context: ActionContext, data: dict
) -> JsonResponse | ActionCall:
    request_id_value = data.get("requestId") or data.get("request_id")
    try:
        request_id = int(request_id_value) if request_id_value is not None else None
    except (TypeError, ValueError):
        return JsonResponse({"detail": "requestId must be an integer"}, status=400)
    if request_id is None:
        request_id = int(uuid.uuid4().int % 1_000_000_000)
    report_value = data.get("report")
    clear_value = data.get("clear")
    if report_value is None or clear_value is None:
        return JsonResponse({"detail": "report and clear required"}, status=400)
    report_flag, error = _coerce_bool(report_value, "report")
    if error:
        return JsonResponse({"detail": error}, status=400)
    clear_flag, error = _coerce_bool(clear_value, "clear")
    if error:
        return JsonResponse({"detail": error}, status=400)
    customer_identifier = data.get("customerIdentifier") or data.get("customer_identifier")
    id_token = data.get("idToken") or data.get("id_token")
    customer_certificate = data.get("customerCertificate") or data.get("customer_certificate")
    if customer_identifier in (None, "") and id_token in (None, "") and customer_certificate in (
        None,
        "",
    ):
        return JsonResponse(
            {"detail": "customerIdentifier, idToken, or customerCertificate required"},
            status=400,
        )
    payload: dict[str, object] = {
        "requestId": request_id,
        "report": bool(report_flag),
        "clear": bool(clear_flag),
    }
    if customer_identifier not in (None, ""):
        payload["customerIdentifier"] = str(customer_identifier)
    if id_token not in (None, ""):
        if not isinstance(id_token, dict):
            return JsonResponse({"detail": "idToken must be an object"}, status=400)
        token_value = id_token.get("idToken") or id_token.get("id_token")
        token_type = id_token.get("type") or id_token.get("tokenType") or id_token.get("token_type")
        if token_value in (None, "") or token_type in (None, ""):
            return JsonResponse({"detail": "idToken.idToken and idToken.type required"}, status=400)
        token_payload: dict[str, object] = {
            "idToken": token_value,
            "type": token_type,
        }
        additional_info = id_token.get("additionalInfo") or id_token.get("additional_info")
        if additional_info not in (None, ""):
            token_payload["additionalInfo"] = additional_info
        payload["idToken"] = token_payload
    if customer_certificate not in (None, ""):
        if not isinstance(customer_certificate, dict):
            return JsonResponse(
                {"detail": "customerCertificate must be an object"}, status=400
            )
        certificate_payload = {
            "hashAlgorithm": customer_certificate.get("hashAlgorithm")
            or customer_certificate.get("hash_algorithm"),
            "issuerNameHash": customer_certificate.get("issuerNameHash")
            or customer_certificate.get("issuer_name_hash"),
            "issuerKeyHash": customer_certificate.get("issuerKeyHash")
            or customer_certificate.get("issuer_key_hash"),
            "serialNumber": customer_certificate.get("serialNumber")
            or customer_certificate.get("serial_number"),
        }
        if any(value in (None, "") for value in certificate_payload.values()):
            return JsonResponse(
                {
                    "detail": (
                        "customerCertificate.hashAlgorithm, issuerNameHash, "
                        "issuerKeyHash, and serialNumber required"
                    )
                },
                status=400,
            )
        payload["customerCertificate"] = certificate_payload
    message_id = uuid.uuid4().hex
    ocpp_action = "CustomerInformation"
    expected_statuses = CALL_EXPECTED_STATUSES.get(ocpp_action)
    msg = json.dumps([2, message_id, ocpp_action, payload])
    async_to_sync(context.ws.send)(msg)
    charger = context.charger or _get_or_create_charger(context.cid, context.connector_value)
    if charger is None:
        return JsonResponse({"detail": "charger not found"}, status=404)
    request_record = CustomerInformationRequest.objects.create(
        charger=charger,
        ocpp_message_id=message_id,
        request_id=request_id,
        payload=payload,
    )
    store.register_pending_call(
        message_id,
        {
            "action": ocpp_action,
            "charger_id": context.cid,
            "connector_id": context.connector_value,
            "log_key": context.log_key,
            "requested_at": timezone.now(),
            "request_id": request_id,
            "request_pk": request_record.pk,
        },
    )
    store.schedule_call_timeout(
        message_id,
        action=ocpp_action,
        log_key=context.log_key,
        message="CustomerInformation request timed out",
    )
    return ActionCall(
        msg=msg,
        message_id=message_id,
        ocpp_action=ocpp_action,
        expected_statuses=expected_statuses,
    )
