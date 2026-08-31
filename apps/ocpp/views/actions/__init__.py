from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from utils.api import api_login_required

from ... import store
from ...models import ChargingProfile
from .common import (
    CALL_EXPECTED_STATUSES,
    ActionCall,
    ActionContext,
    _ensure_charger_access,
    _evaluate_pending_call_result,
    _get_or_create_charger,
    _normalize_connector_slug,
    _parse_request_body,
)
from . import (
    certificates,
    charging_profiles,
    configuration,
    core,
    display_messages,
    firmware,
    monitoring,
    network_profiles,
    reservations,
)
from .registry import ACTION_HANDLERS

__all__ = [
    "ACTION_HANDLERS",
    "ActionCall",
    "ActionContext",
    "ChargingProfile",
    "dispatch_action",
]

_MODULES_FOR_REHYDRATION = (
    certificates,
    charging_profiles,
    configuration,
    core,
    display_messages,
    firmware,
    monitoring,
    network_profiles,
    reservations,
)

for _module in _MODULES_FOR_REHYDRATION:
    for _value in _module.__dict__.values():
        if getattr(_value, "__protocol_calls__", None):
            globals().setdefault(_value.__name__, _value)


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
