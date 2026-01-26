import json
import uuid

from django.http import JsonResponse
from django.utils import timezone

from asgiref.sync import async_to_sync

from apps.protocols.decorators import protocol_call
from apps.protocols.models import ProtocolCall as ProtocolCallModel

from ... import store
from .common import (
    CALL_EXPECTED_STATUSES,
    ActionCall,
    ActionContext,
    _build_component_variable_payload,
)


@protocol_call("ocpp201", ProtocolCallModel.CSMS_TO_CP, "SetMonitoringBase")
def _handle_set_monitoring_base(
    context: ActionContext, data: dict
) -> JsonResponse | ActionCall:
    monitoring_base = data.get("monitoringBase") or data.get("monitoring_base") or data.get("base")
    if monitoring_base in (None, ""):
        return JsonResponse({"detail": "monitoringBase required"}, status=400)
    payload = {"monitoringBase": monitoring_base}
    message_id = uuid.uuid4().hex
    ocpp_action = "SetMonitoringBase"
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
            "monitoring_base": monitoring_base,
        },
    )
    store.schedule_call_timeout(
        message_id,
        action=ocpp_action,
        log_key=context.log_key,
        message="SetMonitoringBase request timed out",
    )
    return ActionCall(
        msg=msg,
        message_id=message_id,
        ocpp_action=ocpp_action,
        expected_statuses=expected_statuses,
    )


@protocol_call("ocpp201", ProtocolCallModel.CSMS_TO_CP, "SetMonitoringLevel")
def _handle_set_monitoring_level(
    context: ActionContext, data: dict
) -> JsonResponse | ActionCall:
    monitoring_level = data.get("severity") or data.get("monitoringLevel") or data.get("monitoring_level")
    try:
        severity = int(monitoring_level)
    except (TypeError, ValueError):
        return JsonResponse({"detail": "severity required"}, status=400)
    payload = {"severity": severity}
    message_id = uuid.uuid4().hex
    ocpp_action = "SetMonitoringLevel"
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
            "monitoring_level": severity,
        },
    )
    store.schedule_call_timeout(
        message_id,
        action=ocpp_action,
        log_key=context.log_key,
        message="SetMonitoringLevel request timed out",
    )
    return ActionCall(
        msg=msg,
        message_id=message_id,
        ocpp_action=ocpp_action,
        expected_statuses=expected_statuses,
    )


@protocol_call("ocpp201", ProtocolCallModel.CSMS_TO_CP, "SetVariableMonitoring")
def _handle_set_variable_monitoring(
    context: ActionContext, data: dict
) -> JsonResponse | ActionCall:
    raw_entries = data.get("setMonitoringData") or data.get("monitoringData") or data.get("set_monitoring_data")
    if not isinstance(raw_entries, (list, tuple)) or not raw_entries:
        return JsonResponse({"detail": "setMonitoringData required"}, status=400)
    entries: list[dict[str, object]] = []
    for entry in raw_entries:
        if not isinstance(entry, dict):
            return JsonResponse({"detail": "setMonitoringData entries must be objects"}, status=400)
        payload_entry, error = _build_component_variable_payload(entry)
        if error:
            return JsonResponse({"detail": error}, status=400)
        variable_monitoring = entry.get("variableMonitoring")
        if not isinstance(variable_monitoring, (list, tuple)) or not variable_monitoring:
            return JsonResponse(
                {"detail": "variableMonitoring required for each entry"},
                status=400,
            )
        payload_entry["variableMonitoring"] = variable_monitoring
        entries.append(payload_entry)
    payload = {"setMonitoringData": entries}
    message_id = uuid.uuid4().hex
    ocpp_action = "SetVariableMonitoring"
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
            "set_monitoring_data": entries,
        },
    )
    store.schedule_call_timeout(
        message_id,
        action=ocpp_action,
        log_key=context.log_key,
        message="SetVariableMonitoring request timed out",
    )
    return ActionCall(
        msg=msg,
        message_id=message_id,
        ocpp_action=ocpp_action,
        expected_statuses=expected_statuses,
    )


@protocol_call("ocpp201", ProtocolCallModel.CSMS_TO_CP, "ClearVariableMonitoring")
def _handle_clear_variable_monitoring(
    context: ActionContext, data: dict
) -> JsonResponse | ActionCall:
    ids_value = data.get("id") or data.get("ids") or data.get("monitoringIds")
    if isinstance(ids_value, (list, tuple)):
        ids = list(ids_value)
    elif ids_value is None:
        ids = []
    else:
        ids = [ids_value]
    if not ids:
        return JsonResponse({"detail": "monitoring ids required"}, status=400)
    normalized_ids: list[int] = []
    for entry in ids:
        try:
            normalized_ids.append(int(entry))
        except (TypeError, ValueError):
            return JsonResponse({"detail": "monitoring ids must be integers"}, status=400)
    payload = {"id": normalized_ids}
    message_id = uuid.uuid4().hex
    ocpp_action = "ClearVariableMonitoring"
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
            "monitoring_ids": normalized_ids,
        },
    )
    store.schedule_call_timeout(
        message_id,
        action=ocpp_action,
        log_key=context.log_key,
        message="ClearVariableMonitoring request timed out",
    )
    return ActionCall(
        msg=msg,
        message_id=message_id,
        ocpp_action=ocpp_action,
        expected_statuses=expected_statuses,
    )


@protocol_call("ocpp201", ProtocolCallModel.CSMS_TO_CP, "GetMonitoringReport")
def _handle_get_monitoring_report(
    context: ActionContext, data: dict
) -> JsonResponse | ActionCall:
    request_id_value = data.get("requestId") or data.get("request_id")
    try:
        request_id = int(request_id_value) if request_id_value is not None else None
    except (TypeError, ValueError):
        return JsonResponse({"detail": "requestId must be an integer"}, status=400)
    if request_id is None:
        request_id = int(uuid.uuid4().int % 1_000_000_000)
    payload: dict[str, object] = {"requestId": request_id}
    report_base = data.get("reportBase")
    if report_base not in (None, ""):
        payload["reportBase"] = report_base
    monitoring_criteria = data.get("monitoringCriteria")
    if monitoring_criteria not in (None, ""):
        if not isinstance(monitoring_criteria, (list, tuple)):
            return JsonResponse({"detail": "monitoringCriteria must be a list"}, status=400)
        payload["monitoringCriteria"] = list(monitoring_criteria)
    component_variable = data.get("componentVariable") or data.get("component_variable")
    if component_variable not in (None, ""):
        if not isinstance(component_variable, (list, tuple)):
            return JsonResponse({"detail": "componentVariable must be a list"}, status=400)
        payload["componentVariable"] = list(component_variable)
    message_id = uuid.uuid4().hex
    ocpp_action = "GetMonitoringReport"
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
    store.register_monitoring_report_request(
        request_id,
        {
            "charger_id": context.cid,
            "connector_id": context.connector_value,
            "requested_at": timezone.now(),
            "message_id": message_id,
        },
    )
    store.schedule_call_timeout(
        message_id,
        action=ocpp_action,
        log_key=context.log_key,
        message="GetMonitoringReport request timed out",
    )
    return ActionCall(
        msg=msg,
        message_id=message_id,
        ocpp_action=ocpp_action,
        expected_statuses=expected_statuses,
    )
