"""Log retrieval tasks for OCPP charge points."""

import json
import logging
import uuid

from asgiref.sync import async_to_sync
from celery import shared_task
from django.utils import timezone

from apps.ocpp import store
from apps.ocpp.models import Charger, ChargerLogRequest
from apps.protocols.decorators import protocol_call
from apps.protocols.models import ProtocolCall as ProtocolCallModel

logger = logging.getLogger(__name__)


@protocol_call("ocpp201", ProtocolCallModel.CSMS_TO_CP, "GetLog")
@protocol_call("ocpp16", ProtocolCallModel.CSMS_TO_CP, "GetLog")
@shared_task(name="apps.ocpp.tasks.request_charge_point_log")
def request_charge_point_log(charger_pk: int, log_type: str = "Diagnostics") -> int:
    """Request logs from a connected charge point via GetLog."""

    try:
        charger = Charger.objects.get(pk=charger_pk)
    except Charger.DoesNotExist:
        logger.warning("Unable to request logs for missing charger %s", charger_pk)
        return 0

    connector_value = charger.connector_id
    ws = store.get_connection(charger.charger_id, connector_value)
    if ws is None:
        logger.info("Charge point %s is not connected; log request skipped", charger.charger_id)
        return 0

    log_type_value = (log_type or "").strip()
    request = ChargerLogRequest.objects.create(
        charger=charger,
        log_type=log_type_value,
        status="Pending",
    )
    message_id = uuid.uuid4().hex
    capture_key = store.start_log_capture(charger.charger_id, connector_value, request.request_id)
    request.message_id = message_id
    request.session_key = capture_key
    request.status = "Requested"
    request.save(update_fields=["message_id", "session_key", "status"])

    payload: dict[str, object] = {"requestId": request.request_id}
    if log_type_value:
        payload["logType"] = log_type_value
    msg = json.dumps([2, message_id, "GetLog", payload])

    log_key = store.identity_key(charger.charger_id, connector_value)

    try:
        async_to_sync(ws.send)(msg)
    except Exception as exc:  # pragma: no cover - network error
        logger.warning("Failed to send GetLog to %s (%s)", charger.charger_id, exc)
        store.finalize_log_capture(capture_key)
        ChargerLogRequest.objects.filter(pk=request.pk).update(
            status="DispatchFailed",
            responded_at=timezone.now(),
        )
        return 0

    store.add_log(log_key, f"< {msg}", log_type="charger")
    store.register_pending_call(
        message_id,
        {
            "action": "GetLog",
            "charger_id": charger.charger_id,
            "connector_id": connector_value,
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
    return request.pk
