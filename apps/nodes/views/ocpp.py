import json
import logging
import uuid
from collections.abc import Mapping
from datetime import timedelta

from asgiref.sync import async_to_sync
from django.http import JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from apps.ocpp import store
from apps.ocpp.models import Charger
from apps.ocpp.network import _parse_remote_datetime

from ..models import Node
from .network import _clean_requester_hint, _load_signed_node

logger = logging.getLogger("apps.nodes.views")


def _require_local_origin(charger: Charger) -> bool:
    local = Node.get_local()
    if not local:
        return charger.node_origin_id is None
    if charger.node_origin_id is None:
        return True
    return charger.node_origin_id == local.pk


def _send_trigger_status(
    charger: Charger, payload: Mapping | None = None
) -> tuple[bool, str, dict[str, object]]:
    connector_value = charger.connector_id
    ws = store.get_connection(charger.charger_id, connector_value)
    if ws is None:
        return False, "no active connection", {}
    payload: dict[str, object] = {"requestedMessage": "StatusNotification"}
    if connector_value is not None:
        payload["connectorId"] = connector_value
    message_id = uuid.uuid4().hex
    msg = json.dumps([2, message_id, "TriggerMessage", payload])
    try:
        async_to_sync(ws.send)(msg)
    except Exception as exc:
        return False, f"failed to send TriggerMessage ({exc})", {}
    log_key = store.identity_key(charger.charger_id, connector_value)
    store.add_log(log_key, f"< {msg}", log_type="charger")
    store.register_pending_call(
        message_id,
        {
            "action": "TriggerMessage",
            "charger_id": charger.charger_id,
            "connector_id": connector_value,
            "log_key": log_key,
            "trigger_target": "StatusNotification",
            "trigger_connector": connector_value,
            "requested_at": timezone.now(),
        },
    )
    store.schedule_call_timeout(
        message_id,
        timeout=5.0,
        action="TriggerMessage",
        log_key=log_key,
        message="TriggerMessage StatusNotification timed out",
    )
    return True, "requested status update", {}


def _send_get_configuration(
    charger: Charger, payload: Mapping | None = None
) -> tuple[bool, str, dict[str, object]]:
    connector_value = charger.connector_id
    ws = store.get_connection(charger.charger_id, connector_value)
    if ws is None:
        return False, "no active connection", {}
    message_id = uuid.uuid4().hex
    msg = json.dumps([2, message_id, "GetConfiguration", {}])
    try:
        async_to_sync(ws.send)(msg)
    except Exception as exc:
        return False, f"failed to send GetConfiguration ({exc})", {}
    log_key = store.identity_key(charger.charger_id, connector_value)
    store.add_log(log_key, f"< {msg}", log_type="charger")
    store.register_pending_call(
        message_id,
        {
            "action": "GetConfiguration",
            "charger_id": charger.charger_id,
            "connector_id": connector_value,
            "log_key": log_key,
            "requested_at": timezone.now(),
        },
    )
    store.schedule_call_timeout(
        message_id,
        timeout=5.0,
        action="GetConfiguration",
        log_key=log_key,
        message=(
            "GetConfiguration timed out: charger did not respond"
            " (operation may not be supported)"
        ),
    )
    return True, "requested configuration update", {}


def _send_reset(
    charger: Charger, payload: Mapping | None = None
) -> tuple[bool, str, dict[str, object]]:
    connector_value = charger.connector_id
    tx = store.get_transaction(charger.charger_id, connector_value)
    if tx:
        return False, "active session in progress", {}
    message_id = uuid.uuid4().hex
    reset_type = None
    if payload:
        reset_type = payload.get("reset_type")
    msg = json.dumps(
        [2, message_id, "Reset", {"type": (reset_type or "Soft")}]
    )
    ws = store.get_connection(charger.charger_id, connector_value)
    if ws is None:
        return False, "no active connection", {}
    try:
        async_to_sync(ws.send)(msg)
    except Exception as exc:
        return False, f"failed to send Reset ({exc})", {}
    log_key = store.identity_key(charger.charger_id, connector_value)
    store.add_log(log_key, f"< {msg}", log_type="charger")
    store.register_pending_call(
        message_id,
        {
            "action": "Reset",
            "charger_id": charger.charger_id,
            "connector_id": connector_value,
            "log_key": log_key,
            "requested_at": timezone.now(),
        },
    )
    store.schedule_call_timeout(
        message_id,
        timeout=5.0,
        action="Reset",
        log_key=log_key,
        message="Reset timed out: charger did not respond",
    )
    return True, "reset requested", {}


def _toggle_rfid(
    charger: Charger, payload: Mapping | None = None
) -> tuple[bool, str, dict[str, object]]:
    enable = None
    if payload is not None:
        enable = payload.get("enable")
    if isinstance(enable, str):
        enable = enable.lower() in {"1", "true", "yes", "on"}
    elif isinstance(enable, (int, bool)):
        enable = bool(enable)
    if enable is None:
        enable = not charger.require_rfid
    enable_bool = bool(enable)
    Charger.objects.filter(pk=charger.pk).update(require_rfid=enable_bool)
    charger.require_rfid = enable_bool
    detail = "RFID authentication enabled" if enable_bool else "RFID authentication disabled"
    return True, detail, {"require_rfid": enable_bool}


def _send_local_rfid_list_remote(
    charger: Charger, payload: Mapping | None = None
) -> tuple[bool, str, dict[str, object]]:
    connector_value = charger.connector_id
    ws = store.get_connection(charger.charger_id, connector_value)
    if ws is None:
        return False, "no active connection", {}
    authorization_list = []
    if payload is not None:
        authorization_list = payload.get("local_authorization_list", []) or []
    if not isinstance(authorization_list, list):
        return False, "local_authorization_list must be a list", {}
    list_version = None
    if payload is not None:
        list_version = payload.get("list_version")
    if list_version is None:
        list_version_value = (charger.local_auth_list_version or 0) + 1
    else:
        try:
            list_version_value = int(list_version)
        except (TypeError, ValueError):
            return False, "invalid list_version", {}
        if list_version_value <= 0:
            return False, "invalid list_version", {}
    update_type = "Full"
    if payload is not None and payload.get("update_type"):
        update_type = str(payload.get("update_type") or "").strip() or "Full"
    message_id = uuid.uuid4().hex
    msg_payload = {
        "listVersion": list_version_value,
        "updateType": update_type,
        "localAuthorizationList": authorization_list,
    }
    msg = json.dumps([2, message_id, "SendLocalList", msg_payload])
    try:
        async_to_sync(ws.send)(msg)
    except Exception as exc:
        return False, f"failed to send SendLocalList ({exc})", {}
    log_key = store.identity_key(charger.charger_id, connector_value)
    store.add_log(log_key, f"< {msg}", log_type="charger")
    store.register_pending_call(
        message_id,
        {
            "action": "SendLocalList",
            "charger_id": charger.charger_id,
            "connector_id": connector_value,
            "log_key": log_key,
            "list_version": list_version_value,
            "list_size": len(authorization_list),
            "requested_at": timezone.now(),
        },
    )
    store.schedule_call_timeout(
        message_id,
        action="SendLocalList",
        log_key=log_key,
        message="SendLocalList request timed out",
    )
    return True, "SendLocalList dispatched", {}


def _get_local_list_version_remote(
    charger: Charger, payload: Mapping | None = None
) -> tuple[bool, str, dict[str, object]]:
    connector_value = charger.connector_id
    ws = store.get_connection(charger.charger_id, connector_value)
    if ws is None:
        return False, "no active connection", {}
    message_id = uuid.uuid4().hex
    msg = json.dumps([2, message_id, "GetLocalListVersion", {}])
    try:
        async_to_sync(ws.send)(msg)
    except Exception as exc:
        return False, f"failed to send GetLocalListVersion ({exc})", {}
    log_key = store.identity_key(charger.charger_id, connector_value)
    store.add_log(log_key, f"< {msg}", log_type="charger")
    store.register_pending_call(
        message_id,
        {
            "action": "GetLocalListVersion",
            "charger_id": charger.charger_id,
            "connector_id": connector_value,
            "log_key": log_key,
            "requested_at": timezone.now(),
        },
    )
    store.schedule_call_timeout(
        message_id,
        action="GetLocalListVersion",
        log_key=log_key,
        message="GetLocalListVersion request timed out",
    )
    return True, "GetLocalListVersion requested", {}


def _change_availability_remote(
    charger: Charger, payload: Mapping | None = None
) -> tuple[bool, str, dict[str, object]]:
    availability_type = None
    if payload is not None:
        availability_type = payload.get("availability_type")
    availability_label = str(availability_type or "").strip()
    if availability_label not in {"Operative", "Inoperative"}:
        return False, "invalid availability type", {}
    connector_value = charger.connector_id
    ws = store.get_connection(charger.charger_id, connector_value)
    if ws is None:
        return False, "no active connection", {}
    connector_id = connector_value if connector_value is not None else 0
    message_id = uuid.uuid4().hex
    msg = json.dumps(
        [
            2,
            message_id,
            "ChangeAvailability",
            {"connectorId": connector_id, "type": availability_label},
        ]
    )
    try:
        async_to_sync(ws.send)(msg)
    except Exception as exc:
        return False, f"failed to send ChangeAvailability ({exc})", {}
    log_key = store.identity_key(charger.charger_id, connector_value)
    store.add_log(log_key, f"< {msg}", log_type="charger")
    timestamp = timezone.now()
    store.register_pending_call(
        message_id,
        {
            "action": "ChangeAvailability",
            "charger_id": charger.charger_id,
            "connector_id": connector_value,
            "availability_type": availability_label,
            "requested_at": timestamp,
        },
    )
    updates = {
        "availability_requested_state": availability_label,
        "availability_requested_at": timestamp,
        "availability_request_status": "",
        "availability_request_status_at": None,
        "availability_request_details": "",
    }
    Charger.objects.filter(pk=charger.pk).update(**updates)
    for field, value in updates.items():
        setattr(charger, field, value)
    return True, f"requested ChangeAvailability {availability_label}", updates


def _clear_cache_remote(
    charger: Charger, payload: Mapping | None = None
) -> tuple[bool, str, dict[str, object]]:
    connector_value = charger.connector_id
    ws = store.get_connection(charger.charger_id, connector_value)
    if ws is None:
        return False, "no active connection", {}
    message_id = uuid.uuid4().hex
    msg = json.dumps([2, message_id, "ClearCache", {}])
    try:
        async_to_sync(ws.send)(msg)
    except Exception as exc:
        return False, f"failed to send ClearCache ({exc})", {}
    log_key = store.identity_key(charger.charger_id, connector_value)
    store.add_log(log_key, f"< {msg}", log_type="charger")
    requested_at = timezone.now()
    store.register_pending_call(
        message_id,
        {
            "action": "ClearCache",
            "charger_id": charger.charger_id,
            "connector_id": connector_value,
            "log_key": log_key,
            "requested_at": requested_at,
        },
    )
    store.schedule_call_timeout(
        message_id,
        action="ClearCache",
        log_key=log_key,
    )
    return True, "requested ClearCache", {}


def _clear_charging_profile_remote(
    charger: Charger, payload: Mapping | None = None
) -> tuple[bool, str, dict[str, object]]:
    connector_value = 0
    ws = store.get_connection(charger.charger_id, connector_value)
    if ws is None:
        return False, "no active connection", {}
    message_id = uuid.uuid4().hex
    msg = json.dumps([2, message_id, "ClearChargingProfile", {}])
    try:
        async_to_sync(ws.send)(msg)
    except Exception as exc:
        return False, f"failed to send ClearChargingProfile ({exc})", {}
    log_key = store.identity_key(charger.charger_id, connector_value)
    store.add_log(log_key, f"< {msg}", log_type="charger")
    requested_at = timezone.now()
    store.register_pending_call(
        message_id,
        {
            "action": "ClearChargingProfile",
            "charger_id": charger.charger_id,
            "connector_id": connector_value,
            "log_key": log_key,
            "requested_at": requested_at,
        },
    )
    store.schedule_call_timeout(
        message_id,
        action="ClearChargingProfile",
        log_key=log_key,
    )
    return True, "requested ClearChargingProfile", {}


def _unlock_connector_remote(
    charger: Charger, payload: Mapping | None = None
) -> tuple[bool, str, dict[str, object]]:
    connector_value = charger.connector_id
    if connector_value in (None, 0):
        return False, "connector id is required", {}
    ws = store.get_connection(charger.charger_id, connector_value)
    if ws is None:
        return False, "no active connection", {}
    message_id = uuid.uuid4().hex
    msg = json.dumps(
        [2, message_id, "UnlockConnector", {"connectorId": connector_value}]
    )
    try:
        async_to_sync(ws.send)(msg)
    except Exception as exc:
        return False, f"failed to send UnlockConnector ({exc})", {}
    log_key = store.identity_key(charger.charger_id, connector_value)
    store.add_log(log_key, f"< {msg}", log_type="charger")
    requested_at = timezone.now()
    store.register_pending_call(
        message_id,
        {
            "action": "UnlockConnector",
            "charger_id": charger.charger_id,
            "connector_id": connector_value,
            "log_key": log_key,
            "requested_at": requested_at,
        },
    )
    store.schedule_call_timeout(
        message_id,
        action="UnlockConnector",
        log_key=log_key,
        message="UnlockConnector request timed out",
    )
    return True, "requested UnlockConnector", {}


def _set_availability_state_remote(
    charger: Charger, payload: Mapping | None = None
) -> tuple[bool, str, dict[str, object]]:
    availability_state = None
    if payload is not None:
        availability_state = payload.get("availability_state")
    availability_label = str(availability_state or "").strip()
    if availability_label not in {"Operative", "Inoperative"}:
        return False, "invalid availability state", {}
    timestamp = timezone.now()
    updates = {
        "availability_state": availability_label,
        "availability_state_updated_at": timestamp,
    }
    Charger.objects.filter(pk=charger.pk).update(**updates)
    for field, value in updates.items():
        setattr(charger, field, value)
    return True, f"availability marked {availability_label}", updates


def _remote_stop_transaction_remote(
    charger: Charger, payload: Mapping | None = None
) -> tuple[bool, str, dict[str, object]]:
    connector_value = charger.connector_id
    ws = store.get_connection(charger.charger_id, connector_value)
    if ws is None:
        return False, "no active connection", {}
    tx_obj = store.get_transaction(charger.charger_id, connector_value)
    if tx_obj is None:
        return False, "no active transaction", {}
    message_id = uuid.uuid4().hex
    msg = json.dumps(
        [
            2,
            message_id,
            "RemoteStopTransaction",
            {"transactionId": tx_obj.pk},
        ]
    )
    try:
        async_to_sync(ws.send)(msg)
    except Exception as exc:
        return False, f"failed to send RemoteStopTransaction ({exc})", {}
    log_key = store.identity_key(charger.charger_id, connector_value)
    store.add_log(log_key, f"< {msg}", log_type="charger")
    store.register_pending_call(
        message_id,
        {
            "action": "RemoteStopTransaction",
            "charger_id": charger.charger_id,
            "connector_id": connector_value,
            "transaction_id": tx_obj.pk,
            "log_key": log_key,
            "requested_at": timezone.now(),
        },
    )
    return True, "remote stop requested", {}


def _prepare_diagnostics_upload_payload(
    request, charger: Charger, payload: Mapping | None
) -> dict[str, object]:
    """Ensure a Media Bucket-backed diagnostics location is present."""

    prepared: dict[str, object] = {}
    if isinstance(payload, Mapping):
        prepared = dict(payload)

    location = str(prepared.get("location") or "").strip()
    if location:
        prepared["location"] = location
        return prepared

    expires_at = timezone.now() + timedelta(days=30)
    bucket = charger.ensure_diagnostics_bucket(expires_at=expires_at)
    upload_path = reverse("ocpp:media-bucket-upload", kwargs={"slug": bucket.slug})
    location = request.build_absolute_uri(upload_path)
    prepared["location"] = location
    if bucket.expires_at:
        prepared.setdefault("stopTime", bucket.expires_at.isoformat())

    Charger.objects.filter(pk=charger.pk).update(
        diagnostics_bucket=bucket, diagnostics_location=location
    )
    charger.diagnostics_bucket = bucket
    charger.diagnostics_location = location
    return prepared


def _request_diagnostics_remote(
    charger: Charger, payload: Mapping | None = None, *, request=None
) -> tuple[bool, str, dict[str, object]]:
    if request is not None:
        payload = _prepare_diagnostics_upload_payload(request, charger, payload)

    location = ""
    stop_time_raw = None
    if isinstance(payload, Mapping):
        location = str(payload.get("location") or "").strip()
        stop_time_raw = payload.get("stopTime")
    if not location:
        return False, "missing upload location", {}

    connector_value = charger.connector_id
    ws = store.get_connection(charger.charger_id, connector_value)
    if ws is None:
        return False, "no active connection", {}

    stop_time_value = _parse_remote_datetime(stop_time_raw)
    message_id = uuid.uuid4().hex
    request_payload: dict[str, object] = {"location": location}
    if stop_time_value:
        request_payload["stopTime"] = stop_time_value.isoformat()
    msg = json.dumps([2, message_id, "GetDiagnostics", request_payload])
    try:
        async_to_sync(ws.send)(msg)
    except Exception as exc:
        return False, f"failed to send GetDiagnostics ({exc})", {}
    log_key = store.identity_key(charger.charger_id, connector_value)
    store.add_log(log_key, f"< {msg}", log_type="charger")
    store.register_pending_call(
        message_id,
        {
            "action": "GetDiagnostics",
            "charger_id": charger.charger_id,
            "connector_id": connector_value,
            "log_key": log_key,
            "location": location,
            "requested_at": timezone.now(),
        },
    )
    return True, "diagnostics requested", {}


REMOTE_ACTIONS = {
    "trigger-status": _send_trigger_status,
    "get-configuration": _send_get_configuration,
    "reset": _send_reset,
    "toggle-rfid": _toggle_rfid,
    "send-local-rfid-list": _send_local_rfid_list_remote,
    "get-local-list-version": _get_local_list_version_remote,
    "change-availability": _change_availability_remote,
    "clear-cache": _clear_cache_remote,
    "clear-charging-profile": _clear_charging_profile_remote,
    "unlock-connector": _unlock_connector_remote,
    "set-availability-state": _set_availability_state_remote,
    "remote-stop": _remote_stop_transaction_remote,
    "request-diagnostics": _request_diagnostics_remote,
}


@csrf_exempt
def network_charger_action(request):
    """Execute remote admin actions on behalf of trusted nodes."""

    if request.method != "POST":
        return JsonResponse({"detail": "POST required"}, status=405)

    try:
        body = json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"detail": "invalid json"}, status=400)

    requester = body.get("requester")
    if not requester:
        return JsonResponse({"detail": "requester required"}, status=400)

    requester_mac = _clean_requester_hint(body.get("requester_mac"))
    requester_public_key = _clean_requester_hint(
        body.get("requester_public_key"), strip=False
    )

    node, error_response = _load_signed_node(
        request,
        requester,
        mac_address=requester_mac,
        public_key=requester_public_key,
    )
    if error_response is not None:
        return error_response

    serial = Charger.normalize_serial(body.get("charger_id"))
    if not serial or Charger.is_placeholder_serial(serial):
        return JsonResponse({"detail": "invalid charger"}, status=400)

    connector = body.get("connector_id")
    if connector in ("", None):
        connector_value = None
    elif isinstance(connector, int):
        connector_value = connector
    else:
        try:
            connector_value = int(str(connector))
        except (TypeError, ValueError):
            return JsonResponse({"detail": "invalid connector"}, status=400)

    charger = Charger.objects.filter(
        charger_id=serial, connector_id=connector_value
    ).first()
    if not charger:
        return JsonResponse({"detail": "charger not found"}, status=404)

    if not charger.allow_remote:
        return JsonResponse({"detail": "remote actions disabled"}, status=403)

    if not _require_local_origin(charger):
        return JsonResponse({"detail": "charger is not managed by this node"}, status=403)

    authorized_node_ids = {
        pk for pk in (charger.manager_node_id, charger.node_origin_id) if pk
    }
    if authorized_node_ids and node and node.pk not in authorized_node_ids:
        return JsonResponse(
            {"detail": "requester does not manage this charger"}, status=403
        )

    action = body.get("action")
    handler = REMOTE_ACTIONS.get(action or "")
    if handler is None:
        return JsonResponse({"detail": "unsupported action"}, status=400)

    if action == "request-diagnostics":
        success, message, updates = handler(charger, body, request=request)
    else:
        success, message, updates = handler(charger, body)

    status_code = 200 if success else 409
    status_label = "ok" if success else "error"
    serialized_updates: dict[str, object] = {}
    if isinstance(updates, Mapping):
        for key, value in updates.items():
            if hasattr(value, "isoformat"):
                serialized_updates[key] = value.isoformat()
            else:
                serialized_updates[key] = value
    return JsonResponse(
        {"status": status_label, "detail": message, "updates": serialized_updates},
        status=status_code,
    )
