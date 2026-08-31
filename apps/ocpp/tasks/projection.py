"""Power projection request and scheduling tasks."""

import json
import logging
import uuid

from asgiref.sync import async_to_sync
from celery import shared_task
from django.utils import timezone

from apps.celery.utils import enqueue_task
from apps.ocpp import store
from apps.ocpp.models import Charger, ChargingProfile, PowerProjection
from apps.protocols.decorators import protocol_call
from apps.protocols.models import ProtocolCall as ProtocolCallModel

logger = logging.getLogger(__name__)


@protocol_call("ocpp16", ProtocolCallModel.CSMS_TO_CP, "GetCompositeSchedule")
@shared_task(name="apps.ocpp.tasks.request_power_projection")
def request_power_projection(
    charger_pk: int,
    duration_seconds: int = 3600,
    charging_rate_unit: str | None = None,
) -> int:
    """Request a composite schedule from a connected charge point."""

    try:
        charger = Charger.objects.get(pk=charger_pk)
    except Charger.DoesNotExist:
        logger.warning("Unable to request composite schedule for missing charger %s", charger_pk)
        return 0

    connector_value = charger.connector_id if charger.connector_id is not None else 0
    ws = store.get_connection(charger.charger_id, charger.connector_id)
    if ws is None:
        logger.info(
            "Charge point %s is not connected; composite schedule request skipped",
            charger.charger_id,
        )
        return 0

    rate_unit = charging_rate_unit or ChargingProfile.RateUnit.WATT
    projection = PowerProjection.objects.create(
        charger=charger,
        connector_id=connector_value,
        duration_seconds=duration_seconds,
        charging_rate_unit=rate_unit,
    )

    message_id = uuid.uuid4().hex
    payload: dict[str, object] = {
        "connectorId": connector_value,
        "duration": duration_seconds,
    }
    if rate_unit:
        payload["chargingRateUnit"] = rate_unit
    msg = json.dumps([2, message_id, "GetCompositeSchedule", payload])

    log_key = store.identity_key(charger.charger_id, charger.connector_id)

    try:
        async_to_sync(ws.send)(msg)
    except Exception as exc:  # pragma: no cover - network error
        logger.warning("Failed to send GetCompositeSchedule to %s (%s)", charger.charger_id, exc)
        projection.status = "Error"
        projection.raw_response = {"error": "send_failed", "message": str(exc)}
        projection.received_at = timezone.now()
        projection.save(update_fields=["status", "raw_response", "received_at", "updated_at"])
        return 0

    store.add_log(log_key, f"< {msg}", log_type="charger")
    store.register_pending_call(
        message_id,
        {
            "action": "GetCompositeSchedule",
            "charger_id": charger.charger_id,
            "connector_id": connector_value,
            "log_key": log_key,
            "projection_pk": projection.pk,
            "requested_at": timezone.now(),
        },
    )
    store.schedule_call_timeout(
        message_id,
        timeout=5.0,
        action="GetCompositeSchedule",
        log_key=log_key,
        message=(
            "GetCompositeSchedule timed out: charger did not respond"
            " (operation may not be supported)"
        ),
    )
    logger.info(
        "Requested composite schedule from charge point %s (connector %s)",
        charger.charger_id,
        connector_value,
    )
    return projection.pk


@shared_task(name="apps.ocpp.tasks.schedule_power_projection_requests")
def schedule_power_projection_requests(
    duration_seconds: int = 3600,
    charging_rate_unit: str = ChargingProfile.RateUnit.WATT,
) -> int:
    """Dispatch GetCompositeSchedule requests for each EVCS."""

    charger_ids = list(
        Charger.objects.filter(
            connector_id__isnull=True,
            power_projection_enabled=True,
        ).values_list("pk", flat=True)
    )
    if not charger_ids:
        logger.debug("No eligible charge points available for power projection")
        return 0

    scheduled = 0
    for charger_pk in charger_ids:
        enqueue_task(
            request_power_projection,
            charger_pk,
            duration_seconds=duration_seconds,
            charging_rate_unit=charging_rate_unit,
            require_enabled=False,
        )
        scheduled += 1

    logger.info("Scheduled power projection requests for %s charge point(s)", scheduled)
    return scheduled
