"""Firmware request and scheduling tasks for charge points."""

import json
import logging
import uuid

from asgiref.sync import async_to_sync
from celery import shared_task
from django.conf import settings

from apps.celery.utils import enqueue_task
from apps.ocpp import store
from apps.ocpp.models import Charger, CPFirmware, CPFirmwareRequest, DataTransferMessage

from .common import DEFAULT_FIRMWARE_VENDOR_ID

logger = logging.getLogger(__name__)


@shared_task(name="apps.ocpp.tasks.request_charge_point_firmware")
def request_charge_point_firmware(charger_pk: int) -> bool:
    """Request firmware metadata from a connected charge point."""

    try:
        charger = Charger.objects.get(pk=charger_pk)
    except Charger.DoesNotExist:
        logger.warning("Unable to request firmware for missing charger %s", charger_pk)
        return False

    connector_value = charger.connector_id
    if CPFirmware.objects.filter(source_charger=charger).exists():
        logger.debug("Skipping firmware request for %s: firmware already recorded", charger.charger_id)
        return False

    if CPFirmwareRequest.objects.filter(charger=charger, responded_at__isnull=True).exists():
        logger.debug("Skipping firmware request for %s: pending request exists", charger.charger_id)
        return False

    ws = store.get_connection(charger.charger_id, connector_value)
    if ws is None:
        logger.info("Charge point %s is not connected; firmware request skipped", charger.charger_id)
        return False

    vendor_setting = getattr(settings, "OCPP_AUTOMATIC_FIRMWARE_VENDOR_ID", DEFAULT_FIRMWARE_VENDOR_ID)
    vendor_id = str(vendor_setting or "").strip() or DEFAULT_FIRMWARE_VENDOR_ID
    message_id = uuid.uuid4().hex
    payload = {"vendorId": vendor_id, "messageId": "DownloadFirmware"}
    msg = json.dumps([2, message_id, "DataTransfer", payload])

    try:
        async_to_sync(ws.send)(msg)
    except Exception as exc:  # pragma: no cover - network error
        logger.warning("Failed to send firmware request to %s (%s)", charger.charger_id, exc)
        return False

    message = DataTransferMessage.objects.create(
        charger=charger,
        connector_id=connector_value,
        direction=DataTransferMessage.DIRECTION_CSMS_TO_CP,
        ocpp_message_id=message_id,
        vendor_id=vendor_id,
        message_id="DownloadFirmware",
        payload=payload,
        status="Pending",
    )
    CPFirmwareRequest.objects.create(
        charger=charger,
        connector_id=connector_value,
        vendor_id=vendor_id,
        message=message,
    )

    log_key = store.identity_key(charger.charger_id, connector_value)
    store.add_log(log_key, "Requested firmware download via DataTransfer.", log_type="charger")
    store.register_pending_call(
        message_id,
        {
            "action": "DataTransfer",
            "charger_id": charger.charger_id,
            "connector_id": connector_value,
            "log_key": log_key,
            "message_pk": message.pk,
        },
    )
    store.schedule_call_timeout(message_id, action="DataTransfer", log_key=log_key)
    logger.info("Requested firmware download from charge point %s", charger.charger_id)
    return True


@shared_task(name="apps.ocpp.tasks.schedule_daily_firmware_snapshot_requests")
def schedule_daily_firmware_snapshot_requests() -> int:
    """Dispatch firmware snapshot requests for eligible charge points."""

    charger_ids = list(
        Charger.objects.filter(
            connector_id__isnull=True,
            firmware_snapshot_enabled=True,
        ).values_list("pk", flat=True)
    )
    if not charger_ids:
        logger.debug("No eligible charge points available for firmware snapshot")
        return 0

    recorded = set(
        CPFirmware.objects.filter(source_charger_id__in=charger_ids).values_list("source_charger_id", flat=True)
    )
    pending = set(
        CPFirmwareRequest.objects.filter(charger_id__in=charger_ids, responded_at__isnull=True).values_list(
            "charger_id", flat=True
        )
    )

    scheduled = 0
    for charger_pk in charger_ids:
        if charger_pk in recorded or charger_pk in pending:
            continue
        enqueue_task(request_charge_point_firmware, charger_pk, require_enabled=False)
        scheduled += 1

    if scheduled:
        logger.info("Scheduled firmware snapshot requests for %s charge point(s)", scheduled)
    else:
        logger.debug("No firmware snapshot requests scheduled; firmware already captured")
    return scheduled
