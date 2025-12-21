import json
import uuid
from datetime import timedelta

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from asgiref.sync import async_to_sync

from utils.api import api_login_required

from apps.protocols.decorators import protocol_call
from apps.protocols.models import ProtocolCall as ProtocolCallModel

from .. import store
from ..models import (
    CPFirmware,
    CPFirmwareDeployment,
    CPReservation,
    Charger,
    ChargingProfile,
    DataTransferMessage,
    CertificateOperation,
    CertificateRequest,
    InstalledCertificate,
)
from .common import (
    CALL_ACTION_LABELS,
    CALL_EXPECTED_STATUSES,
    ActionCall,
    ActionContext,
    _ensure_charger_access,
    _evaluate_pending_call_result,
    _get_or_create_charger,
    _normalize_connector_slug,
    _parse_request_body,
)


@protocol_call("ocpp16", ProtocolCallModel.CSMS_TO_CP, "GetConfiguration")
def _handle_get_configuration(context: ActionContext, data: dict) -> JsonResponse | ActionCall:
    payload: dict[str, object] = {}
    raw_key = data.get("key")
    keys: list[str] = []
    if raw_key not in (None, "", []):
        if isinstance(raw_key, str):
            trimmed = raw_key.strip()
            if trimmed:
                keys.append(trimmed)
        elif isinstance(raw_key, (list, tuple)):
            for entry in raw_key:
                if not isinstance(entry, str):
                    return JsonResponse({"detail": "key entries must be strings"}, status=400)
                entry_text = entry.strip()
                if entry_text:
                    keys.append(entry_text)
        else:
            return JsonResponse({"detail": "key must be a string or list of strings"}, status=400)
        if keys:
            payload["key"] = keys
    message_id = uuid.uuid4().hex
    ocpp_action = "GetConfiguration"
    expected_statuses = CALL_EXPECTED_STATUSES.get(ocpp_action)
    msg = json.dumps([2, message_id, "GetConfiguration", payload])
    return ActionCall(
        msg=msg,
        message_id=message_id,
        ocpp_action=ocpp_action,
        expected_statuses=expected_statuses,
    )


@protocol_call("ocpp16", ProtocolCallModel.CSMS_TO_CP, "ReserveNow")
def _handle_reserve_now(context: ActionContext, data: dict) -> JsonResponse | ActionCall:
    reservation_pk = data.get("reservation") or data.get("reservationId")
    if reservation_pk in (None, ""):
        return JsonResponse({"detail": "reservation required"}, status=400)
    reservation = CPReservation.objects.filter(pk=reservation_pk).first()
    if reservation is None:
        return JsonResponse({"detail": "reservation not found"}, status=404)
    connector_obj = reservation.connector
    if connector_obj is None or connector_obj.connector_id is None:
        detail = _("Unable to determine which connector to reserve.")
        return JsonResponse({"detail": detail}, status=400)
    id_tag = reservation.id_tag_value
    if not id_tag:
        detail = _("Provide an RFID or idTag before creating the reservation.")
        return JsonResponse({"detail": detail}, status=400)
    connector_value = connector_obj.connector_id
    log_key = store.identity_key(context.cid, connector_value)
    ws = store.get_connection(context.cid, connector_value)
    if ws is None:
        return JsonResponse({"detail": "no connection"}, status=404)
    expiry = timezone.localtime(reservation.end_time)
    payload = {
        "connectorId": connector_value,
        "expiryDate": expiry.isoformat(),
        "idTag": id_tag,
        "reservationId": reservation.pk,
    }
    message_id = uuid.uuid4().hex
    ocpp_action = "ReserveNow"
    expected_statuses = CALL_EXPECTED_STATUSES.get(ocpp_action)
    msg = json.dumps([2, message_id, "ReserveNow", payload])
    store.add_log(
        log_key,
        f"ReserveNow request: reservation={reservation.pk}, expiry={expiry.isoformat()}",
        log_type="charger",
    )
    async_to_sync(ws.send)(msg)
    requested_at = timezone.now()
    store.register_pending_call(
        message_id,
        {
            "action": "GetConfiguration",
            "charger_id": context.cid,
            "connector_id": connector_value,
            "log_key": log_key,
            "requested_at": requested_at,
        },
    )
    timeout_message = (
        "GetConfiguration timed out: charger did not respond" " (operation may not be supported)"
    )
    store.schedule_call_timeout(
        message_id,
        timeout=5.0,
        action="GetConfiguration",
        log_key=log_key,
        message=timeout_message,
    )
    store.register_pending_call(
        message_id,
        {
            "action": "ReserveNow",
            "charger_id": context.cid,
            "connector_id": connector_value,
            "log_key": log_key,
            "reservation_pk": reservation.pk,
            "requested_at": requested_at,
        },
    )
    store.schedule_call_timeout(message_id, action="ReserveNow", log_key=log_key)
    reservation.ocpp_message_id = message_id
    reservation.evcs_status = ""
    reservation.evcs_error = ""
    reservation.evcs_confirmed = False
    reservation.evcs_confirmed_at = None
    reservation.save(
        update_fields=[
            "ocpp_message_id",
            "evcs_status",
            "evcs_error",
            "evcs_confirmed",
            "evcs_confirmed_at",
            "updated_on",
        ]
    )
    return ActionCall(
        msg=msg,
        message_id=message_id,
        ocpp_action=ocpp_action,
        expected_statuses=expected_statuses,
        log_key=log_key,
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


@protocol_call("ocpp16", ProtocolCallModel.CSMS_TO_CP, "ChangeAvailability")
def _handle_change_availability(context: ActionContext, data: dict) -> JsonResponse | ActionCall:
    availability_type = data.get("type")
    if availability_type not in {"Operative", "Inoperative"}:
        return JsonResponse({"detail": "invalid availability type"}, status=400)
    connector_payload = context.connector_value if context.connector_value is not None else 0
    if "connectorId" in data:
        candidate = data.get("connectorId")
        if candidate not in (None, ""):
            try:
                connector_payload = int(candidate)
            except (TypeError, ValueError):
                connector_payload = candidate
    message_id = uuid.uuid4().hex
    ocpp_action = "ChangeAvailability"
    expected_statuses = CALL_EXPECTED_STATUSES.get(ocpp_action)
    payload = {"connectorId": connector_payload, "type": availability_type}
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


@protocol_call("ocpp16", ProtocolCallModel.CSMS_TO_CP, "ChangeConfiguration")
def _handle_change_configuration(context: ActionContext, data: dict) -> JsonResponse | ActionCall:
    raw_key = data.get("key")
    if not isinstance(raw_key, str) or not raw_key.strip():
        return JsonResponse({"detail": "key required"}, status=400)
    key_value = raw_key.strip()
    raw_value = data.get("value", None)
    value_included = False
    value_text: str | None = None
    if raw_value is not None:
        if isinstance(raw_value, (str, int, float, bool)):
            value_included = True
            value_text = raw_value if isinstance(raw_value, str) else str(raw_value)
        else:
            return JsonResponse(
                {"detail": "value must be a string, number, or boolean"},
                status=400,
            )
    payload = {"key": key_value}
    if value_included:
        payload["value"] = value_text
    message_id = uuid.uuid4().hex
    ocpp_action = "ChangeConfiguration"
    expected_statuses = CALL_EXPECTED_STATUSES.get(ocpp_action)
    msg = json.dumps([2, message_id, "ChangeConfiguration", payload])
    async_to_sync(context.ws.send)(msg)
    requested_at = timezone.now()
    store.register_pending_call(
        message_id,
        {
            "action": "ChangeConfiguration",
            "charger_id": context.cid,
            "connector_id": context.connector_value,
            "log_key": context.log_key,
            "key": key_value,
            "value": value_text,
            "requested_at": requested_at,
        },
    )
    timeout_message = str(_("Change configuration request timed out."))
    store.schedule_call_timeout(
        message_id,
        action="ChangeConfiguration",
        log_key=context.log_key,
        message=timeout_message,
    )
    if value_included and value_text is not None:
        change_message = str(
            _("Requested configuration change for %(key)s to %(value)s")
            % {"key": key_value, "value": value_text}
        )
    else:
        change_message = str(
            _("Requested configuration change for %(key)s") % {"key": key_value}
        )
    store.add_log(context.log_key, change_message, log_type="charger")
    return ActionCall(
        msg=msg,
        message_id=message_id,
        ocpp_action=ocpp_action,
        expected_statuses=expected_statuses,
    )


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


@protocol_call("ocpp16", ProtocolCallModel.CSMS_TO_CP, "CancelReservation")
def _handle_cancel_reservation(context: ActionContext, data: dict) -> JsonResponse | ActionCall:
    reservation_pk = data.get("reservation") or data.get("reservationId")
    if reservation_pk in (None, ""):
        return JsonResponse({"detail": "reservation required"}, status=400)
    reservation = CPReservation.objects.filter(pk=reservation_pk).first()
    if reservation is None:
        return JsonResponse({"detail": "reservation not found"}, status=404)
    connector_obj = reservation.connector
    if connector_obj is None or connector_obj.connector_id is None:
        detail = _("Unable to determine which connector to cancel.")
        return JsonResponse({"detail": detail}, status=400)
    connector_value = connector_obj.connector_id
    log_key = store.identity_key(context.cid, connector_value)
    ws = store.get_connection(context.cid, connector_value)
    if ws is None:
        return JsonResponse({"detail": "no connection"}, status=404)
    message_id = uuid.uuid4().hex
    ocpp_action = "CancelReservation"
    expected_statuses = CALL_EXPECTED_STATUSES.get(ocpp_action)
    payload = {"reservationId": reservation.pk}
    msg = json.dumps([2, message_id, "CancelReservation", payload])
    store.add_log(
        log_key,
        f"CancelReservation request: reservation={reservation.pk}",
        log_type="charger",
    )
    async_to_sync(ws.send)(msg)
    requested_at = timezone.now()
    store.register_pending_call(
        message_id,
        {
            "action": "CancelReservation",
            "charger_id": context.cid,
            "connector_id": connector_value,
            "log_key": log_key,
            "reservation_pk": reservation.pk,
            "requested_at": requested_at,
        },
    )
    store.schedule_call_timeout(message_id, action="CancelReservation", log_key=log_key)
    reservation.ocpp_message_id = message_id
    reservation.evcs_status = ""
    reservation.evcs_error = ""
    reservation.evcs_confirmed = False
    reservation.evcs_confirmed_at = None
    reservation.save(
        update_fields=[
            "ocpp_message_id",
            "evcs_status",
            "evcs_error",
            "evcs_confirmed",
            "evcs_confirmed_at",
            "updated_on",
        ]
    )
    return ActionCall(
        msg=msg,
        message_id=message_id,
        ocpp_action=ocpp_action,
        expected_statuses=expected_statuses,
        log_key=log_key,
    )


@protocol_call("ocpp201", ProtocolCallModel.CSMS_TO_CP, "UnlockConnector")
@protocol_call("ocpp16", ProtocolCallModel.CSMS_TO_CP, "UnlockConnector")
def _handle_unlock_connector(context: ActionContext, data: dict) -> JsonResponse | ActionCall:
    connector_value: int | None = context.connector_value
    if "connectorId" in data or "connector_id" in data:
        raw_value = data.get("connectorId")
        if raw_value is None:
            raw_value = data.get("connector_id")
        try:
            connector_value = int(raw_value)
        except (TypeError, ValueError):
            return JsonResponse({"detail": "invalid connectorId"}, status=400)

    if connector_value in (None, 0):
        return JsonResponse({"detail": "connector id is required"}, status=400)

    payload = {"connectorId": connector_value}
    message_id = uuid.uuid4().hex
    ocpp_action = "UnlockConnector"
    expected_statuses = CALL_EXPECTED_STATUSES.get(ocpp_action)
    msg = json.dumps([2, message_id, "UnlockConnector", payload])
    async_to_sync(context.ws.send)(msg)
    log_key = store.identity_key(context.cid, connector_value)
    requested_at = timezone.now()
    store.register_pending_call(
        message_id,
        {
            "action": ocpp_action,
            "charger_id": context.cid,
            "connector_id": connector_value,
            "log_key": log_key,
            "requested_at": requested_at,
        },
    )
    store.schedule_call_timeout(
        message_id,
        action=ocpp_action,
        log_key=log_key,
        message="UnlockConnector request timed out",
    )
    return ActionCall(
        msg=msg,
        message_id=message_id,
        ocpp_action=ocpp_action,
        expected_statuses=expected_statuses,
        log_key=log_key,
    )


@protocol_call("ocpp16", ProtocolCallModel.CSMS_TO_CP, "DataTransfer")
def _handle_data_transfer(context: ActionContext, data: dict) -> JsonResponse | ActionCall:
    vendor_id = data.get("vendorId")
    if not isinstance(vendor_id, str) or not vendor_id.strip():
        return JsonResponse({"detail": "vendorId required"}, status=400)
    vendor_id = vendor_id.strip()
    payload: dict[str, object] = {"vendorId": vendor_id}
    message_identifier = ""
    if "messageId" in data and data["messageId"] is not None:
        message_candidate = data["messageId"]
        if not isinstance(message_candidate, str):
            return JsonResponse({"detail": "messageId must be a string"}, status=400)
        message_identifier = message_candidate.strip()
        if message_identifier:
            payload["messageId"] = message_identifier
    if "data" in data:
        payload["data"] = data["data"]
    message_id = uuid.uuid4().hex
    ocpp_action = "DataTransfer"
    expected_statuses = CALL_EXPECTED_STATUSES.get(ocpp_action)
    msg = json.dumps([2, message_id, "DataTransfer", payload])
    record = DataTransferMessage.objects.create(
        charger=context.charger,
        connector_id=context.connector_value,
        direction=DataTransferMessage.DIRECTION_CSMS_TO_CP,
        ocpp_message_id=message_id,
        vendor_id=vendor_id,
        message_id=message_identifier,
        payload=payload,
        status="Pending",
    )
    async_to_sync(context.ws.send)(msg)
    store.register_pending_call(
        message_id,
        {
            "action": "DataTransfer",
            "charger_id": context.cid,
            "connector_id": context.connector_value,
            "message_pk": record.pk,
            "log_key": context.log_key,
        },
    )
    return ActionCall(
        msg=msg,
        message_id=message_id,
        ocpp_action=ocpp_action,
        expected_statuses=expected_statuses,
    )


@protocol_call("ocpp16", ProtocolCallModel.CSMS_TO_CP, "Reset")
def _handle_reset(context: ActionContext, _data: dict) -> JsonResponse | ActionCall:
    tx_obj = store.get_transaction(context.cid, context.connector_value)
    if tx_obj is not None:
        detail = _(
            "Reset is blocked while a charging session is active. "
            "Stop the session first."
        )
        return JsonResponse({"detail": detail}, status=409)
    message_id = uuid.uuid4().hex
    ocpp_action = "Reset"
    expected_statuses = CALL_EXPECTED_STATUSES.get(ocpp_action)
    msg = json.dumps([2, message_id, "Reset", {"type": "Soft"}])
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


@protocol_call("ocpp16", ProtocolCallModel.CSMS_TO_CP, "UpdateFirmware")
def _handle_update_firmware(context: ActionContext, data: dict) -> JsonResponse | ActionCall:
    firmware_id = (
        data.get("firmwareId")
        or data.get("firmware_id")
        or data.get("firmware")
    )
    try:
        firmware_pk = int(firmware_id)
    except (TypeError, ValueError):
        return JsonResponse({"detail": "firmwareId required"}, status=400)

    firmware = CPFirmware.objects.filter(pk=firmware_pk).first()
    if firmware is None:
        return JsonResponse({"detail": "firmware not found"}, status=404)
    if not firmware.has_binary and not firmware.has_json:
        return JsonResponse({"detail": "firmware payload missing"}, status=400)

    retrieve_raw = data.get("retrieveDate") or data.get("retrieve_date")
    if retrieve_raw:
        retrieve_date = parse_datetime(str(retrieve_raw))
        if retrieve_date is None:
            return JsonResponse({"detail": "invalid retrieveDate"}, status=400)
        if timezone.is_naive(retrieve_date):
            retrieve_date = timezone.make_aware(
                retrieve_date, timezone.get_current_timezone()
            )
    else:
        retrieve_date = timezone.now() + timedelta(seconds=30)

    retries_value: int | None = None
    if "retries" in data or "retryCount" in data or "retry_count" in data:
        retries_raw = (
            data.get("retries")
            if "retries" in data
            else data.get("retryCount")
            if "retryCount" in data
            else data.get("retry_count")
        )
        try:
            retries_value = int(retries_raw)
        except (TypeError, ValueError):
            return JsonResponse({"detail": "invalid retries"}, status=400)
        if retries_value < 0:
            return JsonResponse({"detail": "invalid retries"}, status=400)

    retry_interval_value: int | None = None
    if "retryInterval" in data or "retry_interval" in data:
        retry_interval_raw = data.get("retryInterval")
        if retry_interval_raw is None:
            retry_interval_raw = data.get("retry_interval")
        try:
            retry_interval_value = int(retry_interval_raw)
        except (TypeError, ValueError):
            return JsonResponse({"detail": "invalid retryInterval"}, status=400)
        if retry_interval_value < 0:
            return JsonResponse({"detail": "invalid retryInterval"}, status=400)

    message_id = uuid.uuid4().hex
    deployment = CPFirmwareDeployment.objects.create(
        firmware=firmware,
        charger=context.charger,
        node=context.charger.node_origin if context.charger else None,
        ocpp_message_id=message_id,
        status="Pending",
        status_info=_("Awaiting charge point response."),
        status_timestamp=timezone.now(),
        retrieve_date=retrieve_date,
        retry_count=int(retries_value or 0),
        retry_interval=int(retry_interval_value or 0),
        request_payload={},
        is_user_data=True,
    )
    token = deployment.issue_download_token(lifetime=timedelta(hours=4))
    download_path = reverse("ocpp:cp-firmware-download", args=[deployment.pk, token])
    request_obj = getattr(context, "request", None)
    if request_obj is not None:
        download_url = request_obj.build_absolute_uri(download_path)
    else:  # pragma: no cover - defensive fallback for unusual call contexts
        download_url = download_path
    payload = {
        "location": download_url,
        "retrieveDate": retrieve_date.isoformat(),
    }
    if retries_value is not None:
        payload["retries"] = retries_value
    if retry_interval_value is not None:
        payload["retryInterval"] = retry_interval_value
    if firmware.checksum:
        payload["checksum"] = firmware.checksum
    deployment.request_payload = payload
    deployment.save(update_fields=["request_payload", "updated_at"])

    ocpp_action = "UpdateFirmware"
    expected_statuses = CALL_EXPECTED_STATUSES.get(ocpp_action)
    msg = json.dumps([2, message_id, "UpdateFirmware", payload])
    async_to_sync(context.ws.send)(msg)
    store.register_pending_call(
        message_id,
        {
            "action": ocpp_action,
            "charger_id": context.cid,
            "connector_id": context.connector_value,
            "deployment_pk": deployment.pk,
            "log_key": context.log_key,
        },
    )
    store.schedule_call_timeout(
        message_id,
        action=ocpp_action,
        log_key=context.log_key,
        message="UpdateFirmware request timed out",
    )
    return ActionCall(
        msg=msg,
        message_id=message_id,
        ocpp_action=ocpp_action,
        expected_statuses=expected_statuses,
        log_key=context.log_key,
    )


@protocol_call("ocpp201", ProtocolCallModel.CSMS_TO_CP, "SetChargingProfile")
@protocol_call("ocpp16", ProtocolCallModel.CSMS_TO_CP, "SetChargingProfile")
def _handle_set_charging_profile(
    context: ActionContext, data: dict
) -> JsonResponse | ActionCall:
    profile_id = (
        data.get("profileId")
        or data.get("profile_id")
        or data.get("profile")
        or data.get("chargingProfile")
    )
    try:
        profile_pk = int(profile_id)
    except (TypeError, ValueError):
        return JsonResponse({"detail": "profileId required"}, status=400)

    profile = (
        ChargingProfile.objects.select_related("schedule")
        .filter(pk=profile_pk)
        .first()
    )
    if profile is None:
        return JsonResponse({"detail": "charging profile not found"}, status=404)

    connector_value: int | str | None = profile.connector_id
    connector_raw = data.get("connectorId")
    if connector_raw not in (None, ""):
        try:
            connector_value = int(connector_raw)
        except (TypeError, ValueError):
            connector_value = connector_raw
    elif context.connector_value is not None:
        connector_value = context.connector_value

    schedule_override = data.get("schedule") or data.get("chargingSchedule")
    if schedule_override is not None and not isinstance(schedule_override, dict):
        return JsonResponse({"detail": "schedule must be an object"}, status=400)

    payload = profile.as_set_charging_profile_request(
        connector_id=connector_value,
        schedule_payload=schedule_override if isinstance(schedule_override, dict) else None,
    )
    message_id = uuid.uuid4().hex
    ocpp_action = "SetChargingProfile"
    expected_statuses = CALL_EXPECTED_STATUSES.get(ocpp_action)
    msg = json.dumps([2, message_id, ocpp_action, payload])
    async_to_sync(context.ws.send)(msg)
    log_key = context.log_key
    store.register_pending_call(
        message_id,
        {
            "action": ocpp_action,
            "charger_id": context.cid,
            "connector_id": connector_value,
            "charging_profile_id": profile_pk,
            "log_key": log_key,
            "requested_at": timezone.now(),
        },
    )
    store.schedule_call_timeout(
        message_id,
        action=ocpp_action,
        log_key=log_key,
        message="SetChargingProfile request timed out",
    )
    return ActionCall(
        msg=msg,
        message_id=message_id,
        ocpp_action=ocpp_action,
        expected_statuses=expected_statuses,
        log_key=log_key,
    )


@protocol_call("ocpp201", ProtocolCallModel.CSMS_TO_CP, "InstallCertificate")
def _handle_install_certificate(context: ActionContext, data: dict) -> JsonResponse | ActionCall:
    certificate = data.get("certificate")
    if not isinstance(certificate, str) or not certificate.strip():
        return JsonResponse({"detail": "certificate required"}, status=400)
    certificate_type = str(data.get("certificateType") or "").strip()
    payload = {"certificate": certificate.strip()}
    if certificate_type:
        payload["certificateType"] = certificate_type
    message_id = uuid.uuid4().hex
    ocpp_action = "InstallCertificate"
    expected_statuses = CALL_EXPECTED_STATUSES.get(ocpp_action)
    msg = json.dumps([2, message_id, ocpp_action, payload])
    async_to_sync(context.ws.send)(msg)
    log_key = context.log_key
    requested_at = timezone.now()
    operation = CertificateOperation.objects.create(
        charger=context.charger,
        action=CertificateOperation.ACTION_INSTALL,
        certificate_type=certificate_type,
        request_payload=payload,
        status=CertificateOperation.STATUS_PENDING,
    )
    installed_certificate = InstalledCertificate.objects.create(
        charger=context.charger,
        certificate_type=certificate_type,
        certificate=certificate.strip(),
        status=InstalledCertificate.STATUS_PENDING,
        last_action=CertificateOperation.ACTION_INSTALL,
    )
    store.register_pending_call(
        message_id,
        {
            "action": ocpp_action,
            "charger_id": context.cid,
            "connector_id": context.connector_value,
            "log_key": log_key,
            "requested_at": requested_at,
            "operation_pk": operation.pk,
            "installed_certificate_pk": installed_certificate.pk,
        },
    )
    store.schedule_call_timeout(
        message_id,
        action=ocpp_action,
        log_key=log_key,
        message="InstallCertificate request timed out",
    )
    return ActionCall(
        msg=msg,
        message_id=message_id,
        ocpp_action=ocpp_action,
        expected_statuses=expected_statuses,
        log_key=log_key,
    )


@protocol_call("ocpp201", ProtocolCallModel.CSMS_TO_CP, "DeleteCertificate")
def _handle_delete_certificate(context: ActionContext, data: dict) -> JsonResponse | ActionCall:
    hash_data = data.get("certificateHashData")
    if not isinstance(hash_data, dict) or not hash_data:
        return JsonResponse({"detail": "certificateHashData required"}, status=400)
    certificate_type = str(data.get("certificateType") or "").strip()
    payload = {"certificateHashData": hash_data}
    if certificate_type:
        payload["certificateType"] = certificate_type
    message_id = uuid.uuid4().hex
    ocpp_action = "DeleteCertificate"
    expected_statuses = CALL_EXPECTED_STATUSES.get(ocpp_action)
    msg = json.dumps([2, message_id, ocpp_action, payload])
    async_to_sync(context.ws.send)(msg)
    log_key = context.log_key
    requested_at = timezone.now()
    operation = CertificateOperation.objects.create(
        charger=context.charger,
        action=CertificateOperation.ACTION_DELETE,
        certificate_type=certificate_type,
        certificate_hash_data=hash_data,
        request_payload=payload,
        status=CertificateOperation.STATUS_PENDING,
    )
    installed_cert = InstalledCertificate.objects.filter(
        charger=context.charger,
        certificate_hash_data=hash_data,
    ).first()
    if installed_cert:
        installed_cert.status = InstalledCertificate.STATUS_DELETE_PENDING
        installed_cert.last_action = CertificateOperation.ACTION_DELETE
        installed_cert.save(update_fields=["status", "last_action"])
    store.register_pending_call(
        message_id,
        {
            "action": ocpp_action,
            "charger_id": context.cid,
            "connector_id": context.connector_value,
            "log_key": log_key,
            "requested_at": requested_at,
            "operation_pk": operation.pk,
            "installed_certificate_pk": installed_cert.pk if installed_cert else None,
            "certificate_hash_data": hash_data,
        },
    )
    store.schedule_call_timeout(
        message_id,
        action=ocpp_action,
        log_key=log_key,
        message="DeleteCertificate request timed out",
    )
    return ActionCall(
        msg=msg,
        message_id=message_id,
        ocpp_action=ocpp_action,
        expected_statuses=expected_statuses,
        log_key=log_key,
    )


@protocol_call("ocpp201", ProtocolCallModel.CSMS_TO_CP, "CertificateSigned")
def _handle_certificate_signed(context: ActionContext, data: dict) -> JsonResponse | ActionCall:
    certificate_chain = data.get("certificateChain")
    if not isinstance(certificate_chain, str) or not certificate_chain.strip():
        return JsonResponse({"detail": "certificateChain required"}, status=400)
    certificate_type = str(data.get("certificateType") or "").strip()
    payload = {"certificateChain": certificate_chain.strip()}
    if certificate_type:
        payload["certificateType"] = certificate_type
    message_id = uuid.uuid4().hex
    ocpp_action = "CertificateSigned"
    expected_statuses = CALL_EXPECTED_STATUSES.get(ocpp_action)
    msg = json.dumps([2, message_id, ocpp_action, payload])
    async_to_sync(context.ws.send)(msg)
    log_key = context.log_key
    requested_at = timezone.now()
    operation = CertificateOperation.objects.create(
        charger=context.charger,
        action=CertificateOperation.ACTION_SIGNED,
        certificate_type=certificate_type,
        request_payload=payload,
        status=CertificateOperation.STATUS_PENDING,
    )
    request_pk = data.get("requestId") or data.get("certificateRequest")
    if request_pk not in (None, ""):
        try:
            request_pk_value = int(request_pk)
        except (TypeError, ValueError):
            request_pk_value = None
        if request_pk_value:
            CertificateRequest.objects.filter(pk=request_pk_value).update(
                signed_certificate=certificate_chain.strip(),
                status=CertificateRequest.STATUS_PENDING,
                status_info="Certificate sent to charge point.",
            )
    store.register_pending_call(
        message_id,
        {
            "action": ocpp_action,
            "charger_id": context.cid,
            "connector_id": context.connector_value,
            "log_key": log_key,
            "requested_at": requested_at,
            "operation_pk": operation.pk,
        },
    )
    store.schedule_call_timeout(
        message_id,
        action=ocpp_action,
        log_key=log_key,
        message="CertificateSigned request timed out",
    )
    return ActionCall(
        msg=msg,
        message_id=message_id,
        ocpp_action=ocpp_action,
        expected_statuses=expected_statuses,
        log_key=log_key,
    )


@protocol_call("ocpp201", ProtocolCallModel.CSMS_TO_CP, "GetInstalledCertificateIds")
def _handle_get_installed_certificate_ids(
    context: ActionContext, data: dict
) -> JsonResponse | ActionCall:
    certificate_type = data.get("certificateType")
    payload: dict[str, object] = {}
    if certificate_type not in (None, ""):
        payload["certificateType"] = certificate_type
    message_id = uuid.uuid4().hex
    ocpp_action = "GetInstalledCertificateIds"
    expected_statuses = CALL_EXPECTED_STATUSES.get(ocpp_action)
    msg = json.dumps([2, message_id, ocpp_action, payload])
    async_to_sync(context.ws.send)(msg)
    log_key = context.log_key
    requested_at = timezone.now()
    operation = CertificateOperation.objects.create(
        charger=context.charger,
        action=CertificateOperation.ACTION_LIST,
        certificate_type=str(certificate_type or "").strip(),
        request_payload=payload,
        status=CertificateOperation.STATUS_PENDING,
    )
    store.register_pending_call(
        message_id,
        {
            "action": ocpp_action,
            "charger_id": context.cid,
            "connector_id": context.connector_value,
            "log_key": log_key,
            "requested_at": requested_at,
            "operation_pk": operation.pk,
        },
    )
    store.schedule_call_timeout(
        message_id,
        action=ocpp_action,
        log_key=log_key,
        message="GetInstalledCertificateIds request timed out",
    )
    return ActionCall(
        msg=msg,
        message_id=message_id,
        ocpp_action=ocpp_action,
        expected_statuses=expected_statuses,
        log_key=log_key,
    )


ACTION_HANDLERS = {
    "get_configuration": _handle_get_configuration,
    "reserve_now": _handle_reserve_now,
    "remote_stop": _handle_remote_stop,
    "remote_start": _handle_remote_start,
    "get_diagnostics": _handle_get_diagnostics,
    "change_availability": _handle_change_availability,
    "change_configuration": _handle_change_configuration,
    "clear_cache": _handle_clear_cache,
    "cancel_reservation": _handle_cancel_reservation,
    "unlock_connector": _handle_unlock_connector,
    "data_transfer": _handle_data_transfer,
    "reset": _handle_reset,
    "trigger_message": _handle_trigger_message,
    "send_local_list": _handle_send_local_list,
    "get_local_list_version": _handle_get_local_list_version,
    "update_firmware": _handle_update_firmware,
    "set_charging_profile": _handle_set_charging_profile,
    "install_certificate": _handle_install_certificate,
    "delete_certificate": _handle_delete_certificate,
    "certificate_signed": _handle_certificate_signed,
    "get_installed_certificate_ids": _handle_get_installed_certificate_ids,
}

@csrf_exempt
@api_login_required
def dispatch_action(request, cid, connector=None):
    connector_value, _normalized_slug = _normalize_connector_slug(connector)
    log_key = store.identity_key(cid, connector_value)
    charger_obj = _get_or_create_charger(cid, connector_value)
    access_response = _ensure_charger_access(
        request.user, charger_obj, request=request
    )
    if access_response is not None:
        return access_response
    ws = store.get_connection(cid, connector_value)
    if ws is None:
        return JsonResponse({"detail": "no connection"}, status=404)
    data = _parse_request_body(request)
    action = data.get("action")
    handler = ACTION_HANDLERS.get(action)
    if handler is None:
        return JsonResponse({"detail": "unknown action"}, status=400)
    context = ActionContext(
        cid=cid,
        connector_value=connector_value,
        charger=charger_obj,
        ws=ws,
        log_key=log_key,
        request=request,
    )
    result = handler(context, data)
    if isinstance(result, JsonResponse):
        return result
    message_id = result.message_id
    ocpp_action = result.ocpp_action
    msg = result.msg
    log_for_action = result.log_key or log_key
    store.add_log(log_for_action, f"< {msg}", log_type="charger")
    expected_statuses = result.expected_statuses or CALL_EXPECTED_STATUSES.get(
        ocpp_action
    )
    success, detail, status_code = _evaluate_pending_call_result(
        message_id,
        ocpp_action,
        expected_statuses=expected_statuses,
    )
    if not success:
        return JsonResponse({"detail": detail}, status=status_code or 400)
    return JsonResponse({"sent": msg})
