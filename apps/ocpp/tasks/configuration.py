"""Configuration-related OCPP background tasks."""

import json
import logging
import uuid

from asgiref.sync import async_to_sync
from celery import shared_task
from django.utils import timezone

from apps.celery.utils import enqueue_task
from apps.ocpp import store
from apps.ocpp.models import Charger

logger = logging.getLogger(__name__)


@shared_task(name="apps.ocpp.tasks.check_charge_point_configuration")
def check_charge_point_configuration(charger_pk: int) -> bool:
    """Request the latest configuration from a connected charge point."""

    try:
        charger = Charger.objects.get(pk=charger_pk)
    except Charger.DoesNotExist:
        logger.warning("Unable to request configuration for missing charger %s", charger_pk)
        return False

    connector_value = charger.connector_id
    if connector_value is not None:
        logger.debug(
            "Skipping charger %s: connector %s is not eligible for automatic configuration checks",
            charger.charger_id,
            connector_value,
        )
        return False

    ws = store.get_connection(charger.charger_id, connector_value)
    if ws is None:
        logger.info(
            "Charge point %s is not connected; configuration request skipped",
            charger.charger_id,
        )
        return False

    message_id = uuid.uuid4().hex
    payload: dict[str, object] = {}
    msg = json.dumps([2, message_id, "GetConfiguration", payload])

    try:
        async_to_sync(ws.send)(msg)
    except Exception as exc:  # pragma: no cover - network error
        logger.warning("Failed to send GetConfiguration to %s (%s)", charger.charger_id, exc)
        return False

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
    logger.info("Requested configuration from charge point %s", charger.charger_id)
    return True


@shared_task(name="apps.ocpp.tasks.schedule_daily_charge_point_configuration_checks")
def schedule_daily_charge_point_configuration_checks() -> int:
    """Dispatch configuration requests for eligible charge points."""

    charger_ids = list(
        Charger.objects.filter(
            connector_id__isnull=True,
            configuration_check_enabled=True,
        ).values_list("pk", flat=True)
    )
    if not charger_ids:
        logger.debug("No eligible charge points available for configuration check")
        return 0

    scheduled = 0
    for charger_pk in charger_ids:
        enqueue_task(check_charge_point_configuration, charger_pk, require_enabled=False)
        scheduled += 1

    logger.info("Scheduled configuration checks for %s charge point(s)", scheduled)
    return scheduled
