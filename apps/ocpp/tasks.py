import json
import logging
import uuid
from datetime import date, datetime, time, timedelta

from asgiref.sync import async_to_sync
from celery import shared_task
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Q, Prefetch
from django.utils import timezone

from apps.celery.utils import enqueue_task, is_celery_enabled
from apps.emails import mailer
from apps.emails.utils import resolve_recipient_fallbacks
from apps.nodes.models import Node
from apps.protocols.decorators import protocol_call
from apps.protocols.models import ProtocolCall as ProtocolCallModel

from . import store
from .forwarder import forwarder
from .models import (
    Charger,
    MeterValue,
    Transaction,
    ChargingProfile,
    ChargerLogRequest,
    DataTransferMessage,
    CPFirmware,
    CPFirmwareRequest,
    PowerProjection,
)
logger = logging.getLogger(__name__)


DEFAULT_FIRMWARE_VENDOR_ID = "org.openchargealliance.firmware"


@shared_task
def check_charge_point_configuration(charger_pk: int) -> bool:
    """Request the latest configuration from a connected charge point."""

    try:
        charger = Charger.objects.get(pk=charger_pk)
    except Charger.DoesNotExist:
        logger.warning(
            "Unable to request configuration for missing charger %s",
            charger_pk,
        )
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
        logger.warning(
            "Failed to send GetConfiguration to %s (%s)",
            charger.charger_id,
            exc,
        )
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
    logger.info(
        "Requested configuration from charge point %s",
        charger.charger_id,
    )
    return True


@protocol_call("ocpp201", ProtocolCallModel.CSMS_TO_CP, "GetLog")
@protocol_call("ocpp16", ProtocolCallModel.CSMS_TO_CP, "GetLog")
@shared_task
def request_charge_point_log(charger_pk: int, log_type: str = "Diagnostics") -> int:
    """Request logs from a connected charge point via GetLog."""

    try:
        charger = Charger.objects.get(pk=charger_pk)
    except Charger.DoesNotExist:
        logger.warning(
            "Unable to request logs for missing charger %s",
            charger_pk,
        )
        return 0

    connector_value = charger.connector_id
    ws = store.get_connection(charger.charger_id, connector_value)
    if ws is None:
        logger.info(
            "Charge point %s is not connected; log request skipped",
            charger.charger_id,
        )
        return 0

    log_type_value = (log_type or "").strip()
    request = ChargerLogRequest.objects.create(
        charger=charger,
        log_type=log_type_value,
        status="Pending",
    )
    message_id = uuid.uuid4().hex
    capture_key = store.start_log_capture(
        charger.charger_id,
        connector_value,
        request.request_id,
    )
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
        logger.warning(
            "Failed to send GetLog to %s (%s)",
            charger.charger_id,
            exc,
        )
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


@shared_task
def request_charge_point_firmware(charger_pk: int) -> bool:
    """Request firmware metadata from a connected charge point."""

    try:
        charger = Charger.objects.get(pk=charger_pk)
    except Charger.DoesNotExist:
        logger.warning(
            "Unable to request firmware for missing charger %s",
            charger_pk,
        )
        return False

    connector_value = charger.connector_id
    if CPFirmware.objects.filter(source_charger=charger).exists():
        logger.debug(
            "Skipping firmware request for %s: firmware already recorded",
            charger.charger_id,
        )
        return False

    if CPFirmwareRequest.objects.filter(
        charger=charger, responded_at__isnull=True
    ).exists():
        logger.debug(
            "Skipping firmware request for %s: pending request exists",
            charger.charger_id,
        )
        return False

    ws = store.get_connection(charger.charger_id, connector_value)
    if ws is None:
        logger.info(
            "Charge point %s is not connected; firmware request skipped",
            charger.charger_id,
        )
        return False

    vendor_setting = getattr(
        settings, "OCPP_AUTOMATIC_FIRMWARE_VENDOR_ID", DEFAULT_FIRMWARE_VENDOR_ID
    )
    vendor_id = str(vendor_setting or "").strip() or DEFAULT_FIRMWARE_VENDOR_ID
    message_id = uuid.uuid4().hex
    payload = {"vendorId": vendor_id, "messageId": "DownloadFirmware"}
    msg = json.dumps([2, message_id, "DataTransfer", payload])

    try:
        async_to_sync(ws.send)(msg)
    except Exception as exc:  # pragma: no cover - network error
        logger.warning(
            "Failed to send firmware request to %s (%s)",
            charger.charger_id,
            exc,
        )
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
    store.add_log(
        log_key,
        "Requested firmware download via DataTransfer.",
        log_type="charger",
    )
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
    store.schedule_call_timeout(
        message_id,
        action="DataTransfer",
        log_key=log_key,
    )
    logger.info(
        "Requested firmware download from charge point %s",
        charger.charger_id,
    )
    return True


@shared_task
def schedule_daily_firmware_snapshot_requests() -> int:
    """Dispatch firmware snapshot requests for eligible charge points."""

    charger_ids = list(
        Charger.objects.filter(
            connector_id__isnull=True, firmware_snapshot_enabled=True
        ).values_list("pk", flat=True)
    )
    if not charger_ids:
        logger.debug("No eligible charge points available for firmware snapshot")
        return 0

    recorded = set(
        CPFirmware.objects.filter(source_charger_id__in=charger_ids)
        .values_list("source_charger_id", flat=True)
    )
    pending = set(
        CPFirmwareRequest.objects.filter(
            charger_id__in=charger_ids, responded_at__isnull=True
        ).values_list("charger_id", flat=True)
    )

    scheduled = 0
    for charger_pk in charger_ids:
        if charger_pk in recorded or charger_pk in pending:
            continue
        enqueue_task(request_charge_point_firmware, charger_pk, require_enabled=False)
        scheduled += 1

    if scheduled:
        logger.info(
            "Scheduled firmware snapshot requests for %s charge point(s)",
            scheduled,
        )
    else:
        logger.debug(
            "No firmware snapshot requests scheduled; firmware already captured",
        )
    return scheduled


@shared_task
def schedule_daily_charge_point_configuration_checks() -> int:
    """Dispatch configuration requests for eligible charge points."""

    charger_ids = list(
        Charger.objects.filter(
            connector_id__isnull=True, configuration_check_enabled=True
        ).values_list("pk", flat=True)
    )
    if not charger_ids:
        logger.debug("No eligible charge points available for configuration check")
        return 0

    scheduled = 0
    for charger_pk in charger_ids:
        enqueue_task(check_charge_point_configuration, charger_pk, require_enabled=False)
        scheduled += 1
    logger.info(
        "Scheduled configuration checks for %s charge point(s)", scheduled
    )
    return scheduled


@protocol_call("ocpp16", ProtocolCallModel.CSMS_TO_CP, "GetCompositeSchedule")
@shared_task
def request_power_projection(
    charger_pk: int,
    duration_seconds: int = 3600,
    charging_rate_unit: str | None = None,
) -> int:
    """Request a composite schedule from a connected charge point."""

    try:
        charger = Charger.objects.get(pk=charger_pk)
    except Charger.DoesNotExist:
        logger.warning(
            "Unable to request composite schedule for missing charger %s",
            charger_pk,
        )
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
        logger.warning(
            "Failed to send GetCompositeSchedule to %s (%s)",
            charger.charger_id,
            exc,
        )
        projection.status = "Error"
        projection.raw_response = {
            "error": "send_failed",
            "message": str(exc),
        }
        projection.received_at = timezone.now()
        projection.save(
            update_fields=["status", "raw_response", "received_at", "updated_at"]
        )
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


@shared_task
def schedule_power_projection_requests(
    duration_seconds: int = 3600,
    charging_rate_unit: str = ChargingProfile.RateUnit.WATT,
) -> int:
    """Dispatch GetCompositeSchedule requests for each EVCS."""

    charger_ids = list(
        Charger.objects.filter(
            connector_id__isnull=True, power_projection_enabled=True
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

    logger.info(
        "Scheduled power projection requests for %s charge point(s)",
        scheduled,
    )
    return scheduled


@shared_task
def purge_meter_values() -> int:
    """Delete meter values older than 7 days.

    Values tied to transactions without a recorded meter_stop are preserved so
    that ongoing or incomplete sessions retain their energy data.
    Returns the number of deleted rows.
    """
    cutoff = timezone.now() - timedelta(days=7)
    qs = MeterValue.objects.filter(timestamp__lt=cutoff).filter(
        Q(transaction__isnull=True) | Q(transaction__meter_stop__isnull=False)
    )
    deleted, _ = qs.delete()
    logger.info("Purged %s meter values", deleted)
    return deleted


# Backwards compatibility alias
purge_meter_readings = purge_meter_values


# The forwarding task previously used a "1/10m" rate limit that Celery 5.4+ no
# longer parses (it expects a modifier of "s", "m", or "h").  Using an hourly
# equivalent keeps the intent of running once every five minutes while remaining
# compatible with Celery's parser.
@shared_task(rate_limit="12/h")
def setup_forwarders() -> int:
    """Ensure websocket connections exist for forwarded charge points."""

    connected = forwarder.sync_forwarded_charge_points()
    if not connected:
        logger.debug("Forwarding synchronization completed with no new sessions")
    return connected


# Backwards compatibility alias for legacy schedules
@shared_task
def push_forwarded_charge_points() -> int:
    """Legacy forwarding task retained for older schedules."""

    return setup_forwarders()


# Backwards compatibility alias for legacy schedules
@shared_task(name="apps.ocpp.tasks.sync_remote_chargers")
def sync_remote_chargers() -> int:
    """Maintain the legacy task name used by older beat schedules."""

    return setup_forwarders()


def _resolve_report_window() -> tuple[datetime, datetime, date]:
    """Return the start/end datetimes for today's reporting window."""

    current_tz = timezone.get_current_timezone()
    today = timezone.localdate()
    start = timezone.make_aware(datetime.combine(today, time.min), current_tz)
    end = start + timedelta(days=1)
    return start, end, today


def _session_report_recipients() -> list[str]:
    """Return the list of recipients for the daily session report."""
    recipients, _ = resolve_recipient_fallbacks([], owner=None)
    return recipients


def _format_duration(delta: timedelta | None) -> str:
    """Return a compact string for ``delta`` or ``"in progress"``."""

    if delta is None:
        return "in progress"
    total_seconds = int(delta.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if seconds or not parts:
        parts.append(f"{seconds}s")
    return " ".join(parts)


def _format_charger(transaction: Transaction) -> str:
    """Return a human friendly label for ``transaction``'s charger."""

    charger = transaction.charger
    if charger is None:
        return "Unknown charger"
    for attr in ("display_name", "name", "charger_id"):
        value = getattr(charger, attr, "")
        if value:
            return str(value)
    return str(charger)


@shared_task
def send_daily_session_report() -> int:
    """Send a summary of today's OCPP sessions when email is available."""

    if not mailer.can_send_email():
        logger.info("Skipping OCPP session report: email not configured")
        return 0

    if not is_celery_enabled():
        logger.info("Skipping OCPP session report: celery feature disabled")
        return 0

    recipients = _session_report_recipients()
    if not recipients:
        logger.info("Skipping OCPP session report: no recipients found")
        return 0

    start, end, today = _resolve_report_window()
    meter_value_prefetch = Prefetch(
        "meter_values",
        queryset=MeterValue.objects.filter(energy__isnull=False).order_by("timestamp"),
        to_attr="prefetched_meter_values",
    )
    transactions = list(
        Transaction.objects.filter(start_time__gte=start, start_time__lt=end)
        .select_related("charger", "account")
        .prefetch_related(meter_value_prefetch)
        .order_by("start_time")
    )
    if not transactions:
        logger.info("No OCPP sessions recorded on %s", today.isoformat())
        return 0

    total_energy = sum(transaction.kw for transaction in transactions)
    lines = [
        f"OCPP session report for {today.isoformat()}",
        "",
        f"Total sessions: {len(transactions)}",
        f"Total energy: {total_energy:.2f} kWh",
        "",
    ]

    for index, transaction in enumerate(transactions, start=1):
        start_local = timezone.localtime(transaction.start_time)
        stop_local = (
            timezone.localtime(transaction.stop_time)
            if transaction.stop_time
            else None
        )
        duration = _format_duration(
            stop_local - start_local if stop_local else None
        )
        account = transaction.account.name if transaction.account else "N/A"
        connector_letter = Charger.connector_letter_from_value(
            transaction.connector_id
        )
        connector = (
            f"Connector {connector_letter}"
            if connector_letter
            else None
        )
        lines.append(f"{index}. {_format_charger(transaction)}")
        lines.append(f"   Account: {account}")
        if transaction.rfid:
            lines.append(f"   RFID: {transaction.rfid}")
        identifier = transaction.vehicle_identifier
        if identifier:
            label = "VID" if transaction.vehicle_identifier_source == "vid" else "VIN"
            lines.append(f"   {label}: {identifier}")
        if connector:
            lines.append(f"   {connector}")
        lines.append(
            "   Start: "
            f"{start_local.strftime('%H:%M:%S %Z')}"
        )
        if stop_local:
            lines.append(
                "   Stop: "
                f"{stop_local.strftime('%H:%M:%S %Z')} ({duration})"
            )
        else:
            lines.append("   Stop: in progress")
        lines.append(f"   Energy: {transaction.kw:.2f} kWh")
        lines.append("")

    subject = f"OCPP session report for {today.isoformat()}"
    body = "\n".join(lines).strip()

    node = Node.get_local()
    if node is not None:
        node.send_mail(subject, body, recipients)
    else:
        mailer.send(
            subject,
            body,
            recipients,
            getattr(settings, "DEFAULT_FROM_EMAIL", None),
        )

    logger.info(
        "Sent OCPP session report for %s to %s", today.isoformat(), ", ".join(recipients)
    )
    return len(transactions)
