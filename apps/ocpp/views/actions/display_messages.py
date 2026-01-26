import json
import uuid

from django.http import JsonResponse
from django.utils import timezone

from asgiref.sync import async_to_sync

from apps.protocols.decorators import protocol_call
from apps.protocols.models import ProtocolCall as ProtocolCallModel

from ... import store
from .common import CALL_EXPECTED_STATUSES, ActionCall, ActionContext


@protocol_call("ocpp201", ProtocolCallModel.CSMS_TO_CP, "ClearDisplayMessage")
def _handle_clear_display_message(
    context: ActionContext, data: dict
) -> JsonResponse | ActionCall:
    message_id_value = data.get("id") or data.get("messageId") or data.get("message_id")
    try:
        display_message_id = int(message_id_value)
    except (TypeError, ValueError):
        return JsonResponse({"detail": "id required"}, status=400)
    payload = {"id": display_message_id}
    message_id = uuid.uuid4().hex
    ocpp_action = "ClearDisplayMessage"
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
            "display_message_id": display_message_id,
        },
    )
    store.schedule_call_timeout(
        message_id,
        action=ocpp_action,
        log_key=context.log_key,
        message="ClearDisplayMessage request timed out",
    )
    return ActionCall(
        msg=msg,
        message_id=message_id,
        ocpp_action=ocpp_action,
        expected_statuses=expected_statuses,
    )


@protocol_call("ocpp201", ProtocolCallModel.CSMS_TO_CP, "GetDisplayMessages")
def _handle_get_display_messages(
    context: ActionContext, data: dict
) -> JsonResponse | ActionCall:
    request_id_value = data.get("requestId") or data.get("request_id")
    try:
        request_id = int(request_id_value) if request_id_value is not None else None
    except (TypeError, ValueError):
        return JsonResponse({"detail": "requestId must be an integer"}, status=400)
    if request_id is None:
        request_id = int(uuid.uuid4().int % 1_000_000_000)
    ids_value = (
        data.get("id")
        or data.get("ids")
        or data.get("messageIds")
        or data.get("message_ids")
    )
    payload: dict[str, object] = {"requestId": request_id}
    if ids_value not in (None, ""):
        if isinstance(ids_value, (list, tuple)):
            ids = list(ids_value)
        else:
            ids = [ids_value]
        if not ids:
            return JsonResponse({"detail": "id list must not be empty"}, status=400)
        normalized_ids: list[int] = []
        for entry in ids:
            try:
                normalized_ids.append(int(entry))
            except (TypeError, ValueError):
                return JsonResponse({"detail": "id values must be integers"}, status=400)
        payload["id"] = normalized_ids
    priority = data.get("priority")
    if priority not in (None, ""):
        payload["priority"] = priority
    state = data.get("state")
    if state not in (None, ""):
        payload["state"] = state
    message_id = uuid.uuid4().hex
    ocpp_action = "GetDisplayMessages"
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
        message="GetDisplayMessages request timed out",
    )
    return ActionCall(
        msg=msg,
        message_id=message_id,
        ocpp_action=ocpp_action,
        expected_statuses=expected_statuses,
    )


@protocol_call("ocpp201", ProtocolCallModel.CSMS_TO_CP, "SetDisplayMessage")
def _handle_set_display_message(
    context: ActionContext, data: dict
) -> JsonResponse | ActionCall:
    message_payload = data.get("message")
    if message_payload is None:
        message_payload = {}
        message_id_value = data.get("id") or data.get("messageId") or data.get("message_id")
        if message_id_value not in (None, ""):
            message_payload["id"] = message_id_value
        priority = data.get("priority")
        if priority not in (None, ""):
            message_payload["priority"] = priority
        state = data.get("state")
        if state not in (None, ""):
            message_payload["state"] = state
        display = data.get("display")
        if display not in (None, ""):
            message_payload["display"] = display
        start_datetime = data.get("startDateTime") or data.get("start_date_time")
        if start_datetime not in (None, ""):
            message_payload["startDateTime"] = start_datetime
        end_datetime = data.get("endDateTime") or data.get("end_date_time")
        if end_datetime not in (None, ""):
            message_payload["endDateTime"] = end_datetime
        transaction_id = data.get("transactionId") or data.get("transaction_id")
        if transaction_id not in (None, ""):
            message_payload["transactionId"] = transaction_id
        content_payload = data.get("messageContent") or data.get("message_content")
        if content_payload is None:
            content_value = data.get("content") or data.get("text")
            format_value = data.get("format") or data.get("messageFormat")
            language_value = data.get("language")
            if content_value not in (None, "") or format_value not in (None, ""):
                content_payload = {
                    "content": content_value,
                    "format": format_value,
                }
                if language_value not in (None, ""):
                    content_payload["language"] = language_value
        if content_payload is not None:
            message_payload["message"] = content_payload
    if not isinstance(message_payload, dict):
        return JsonResponse({"detail": "message must be an object"}, status=400)
    display_message_id = message_payload.get("id") or message_payload.get("messageId")
    try:
        message_id_value = int(display_message_id)
    except (TypeError, ValueError):
        return JsonResponse({"detail": "message.id required"}, status=400)
    priority = message_payload.get("priority")
    if priority in (None, ""):
        return JsonResponse({"detail": "message.priority required"}, status=400)
    content_payload = message_payload.get("message") or message_payload.get("messageContent")
    if isinstance(content_payload, dict):
        if content_payload.get("format") in (None, "") or content_payload.get("content") in (
            None,
            "",
        ):
            return JsonResponse(
                {"detail": "message.message.format and message.message.content required"},
                status=400,
            )
    else:
        return JsonResponse({"detail": "message.message must be an object"}, status=400)
    message_payload["id"] = message_id_value
    message_payload["message"] = content_payload
    payload = {"message": message_payload}
    message_id = uuid.uuid4().hex
    ocpp_action = "SetDisplayMessage"
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
            "display_message_id": message_id_value,
        },
    )
    store.schedule_call_timeout(
        message_id,
        action=ocpp_action,
        log_key=context.log_key,
        message="SetDisplayMessage request timed out",
    )
    return ActionCall(
        msg=msg,
        message_id=message_id,
        ocpp_action=ocpp_action,
        expected_statuses=expected_statuses,
    )
