import json
import uuid

from django.http import JsonResponse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from asgiref.sync import async_to_sync

from apps.protocols.decorators import protocol_call
from apps.protocols.models import ProtocolCall as ProtocolCallModel

from ... import store
from .common import (
    CALL_EXPECTED_STATUSES,
    ActionCall,
    ActionContext,
    _build_component_variable_entry,
    _build_component_variable_payload,
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


@protocol_call("ocpp201", ProtocolCallModel.CSMS_TO_CP, "GetVariables")
def _handle_get_variables(context: ActionContext, data: dict) -> JsonResponse | ActionCall:
    raw_entries = data.get("getVariableData") or data.get("variables") or data.get("get_variable_data")
    if not isinstance(raw_entries, (list, tuple)) or not raw_entries:
        return JsonResponse({"detail": "getVariableData required"}, status=400)
    entries: list[dict[str, object]] = []
    for entry in raw_entries:
        if not isinstance(entry, dict):
            return JsonResponse({"detail": "getVariableData entries must be objects"}, status=400)
        payload_entry, error = _build_component_variable_payload(entry)
        if error:
            return JsonResponse({"detail": error}, status=400)
        entries.append(payload_entry)
    payload = {"getVariableData": entries}
    message_id = uuid.uuid4().hex
    ocpp_action = "GetVariables"
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
            "requested_at": timezone.now(),
        },
    )
    store.schedule_call_timeout(
        message_id,
        action=ocpp_action,
        log_key=context.log_key,
        message="GetVariables request timed out",
    )
    return ActionCall(
        msg=msg,
        message_id=message_id,
        ocpp_action=ocpp_action,
        expected_statuses=expected_statuses,
    )


@protocol_call("ocpp201", ProtocolCallModel.CSMS_TO_CP, "SetVariables")
def _handle_set_variables(context: ActionContext, data: dict) -> JsonResponse | ActionCall:
    raw_entries = data.get("setVariableData") or data.get("variables") or data.get("set_variable_data")
    if not isinstance(raw_entries, (list, tuple)) or not raw_entries:
        return JsonResponse({"detail": "setVariableData required"}, status=400)
    entries: list[dict[str, object]] = []
    for entry in raw_entries:
        if not isinstance(entry, dict):
            return JsonResponse({"detail": "setVariableData entries must be objects"}, status=400)
        attribute_value = entry.get("attributeValue")
        if attribute_value in (None, ""):
            return JsonResponse({"detail": "attributeValue required"}, status=400)
        payload_entry, error = _build_component_variable_payload(entry)
        if error:
            return JsonResponse({"detail": error}, status=400)
        payload_entry["attributeValue"] = attribute_value
        entries.append(payload_entry)
    payload = {"setVariableData": entries}
    message_id = uuid.uuid4().hex
    ocpp_action = "SetVariables"
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
            "requested_at": timezone.now(),
            "set_variable_data": entries,
        },
    )
    store.schedule_call_timeout(
        message_id,
        action=ocpp_action,
        log_key=context.log_key,
        message="SetVariables request timed out",
    )
    return ActionCall(
        msg=msg,
        message_id=message_id,
        ocpp_action=ocpp_action,
        expected_statuses=expected_statuses,
    )


@protocol_call("ocpp201", ProtocolCallModel.CSMS_TO_CP, "GetBaseReport")
def _handle_get_base_report(context: ActionContext, data: dict) -> JsonResponse | ActionCall:
    request_id_value = data.get("requestId") or data.get("request_id")
    try:
        request_id = int(request_id_value) if request_id_value is not None else None
    except (TypeError, ValueError):
        return JsonResponse({"detail": "requestId must be an integer"}, status=400)
    if request_id is None:
        request_id = int(uuid.uuid4().int % 1_000_000_000)
    report_base = data.get("reportBase") or data.get("report_base")
    if report_base in (None, ""):
        return JsonResponse({"detail": "reportBase required"}, status=400)
    payload = {"requestId": request_id, "reportBase": report_base}
    message_id = uuid.uuid4().hex
    ocpp_action = "GetBaseReport"
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
            "requested_at": timezone.now(),
            "request_id": request_id,
        },
    )
    store.schedule_call_timeout(
        message_id,
        action=ocpp_action,
        log_key=context.log_key,
        message="GetBaseReport request timed out",
    )
    return ActionCall(
        msg=msg,
        message_id=message_id,
        ocpp_action=ocpp_action,
        expected_statuses=expected_statuses,
    )


@protocol_call("ocpp201", ProtocolCallModel.CSMS_TO_CP, "GetReport")
def _handle_get_report(context: ActionContext, data: dict) -> JsonResponse | ActionCall:
    request_id_value = data.get("requestId") or data.get("request_id")
    try:
        request_id = int(request_id_value) if request_id_value is not None else None
    except (TypeError, ValueError):
        return JsonResponse({"detail": "requestId must be an integer"}, status=400)
    if request_id is None:
        request_id = int(uuid.uuid4().int % 1_000_000_000)
    payload: dict[str, object] = {"requestId": request_id}
    component_criteria = data.get("componentCriteria") or data.get("component_criteria")
    if component_criteria not in (None, ""):
        if not isinstance(component_criteria, (list, tuple)) or not component_criteria:
            return JsonResponse({"detail": "componentCriteria must be a list"}, status=400)
        payload["componentCriteria"] = list(component_criteria)
    component_variable = data.get("componentVariable") or data.get("component_variable")
    if component_variable not in (None, ""):
        if not isinstance(component_variable, (list, tuple)) or not component_variable:
            return JsonResponse({"detail": "componentVariable must be a list"}, status=400)
        entries: list[dict[str, object]] = []
        for entry in component_variable:
            if not isinstance(entry, dict):
                return JsonResponse(
                    {"detail": "componentVariable entries must be objects"}, status=400
                )
            payload_entry, error = _build_component_variable_entry(entry)
            if error:
                return JsonResponse({"detail": error}, status=400)
            entries.append(payload_entry)
        payload["componentVariable"] = entries
    message_id = uuid.uuid4().hex
    ocpp_action = "GetReport"
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
            "requested_at": timezone.now(),
            "request_id": request_id,
        },
    )
    store.schedule_call_timeout(
        message_id,
        action=ocpp_action,
        log_key=context.log_key,
        message="GetReport request timed out",
    )
    return ActionCall(
        msg=msg,
        message_id=message_id,
        ocpp_action=ocpp_action,
        expected_statuses=expected_statuses,
    )
