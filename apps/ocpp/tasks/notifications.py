"""Notification and reporting tasks for OCPP entities."""

import logging
from datetime import date, datetime, time, timedelta

from celery import shared_task
from django.conf import settings
from django.db.models import Prefetch
from django.db.models.functions import Coalesce, Greatest
from django.utils import timezone

from apps.celery.utils import is_celery_enabled
from apps.emails import mailer
from apps.nodes.models import Node
from apps.ocpp.models import Charger, MeterValue, Transaction

from .common import (
    OFFLINE_NOTIFICATION_COOLDOWN,
    OFFLINE_NOTIFICATION_GRACE,
    resolve_offline_notification_recipient,
    session_report_recipients,
)

logger = logging.getLogger(__name__)


def _resolve_report_window() -> tuple[datetime, datetime, date]:
    """Return the start/end datetimes for today's reporting window."""

    current_tz = timezone.get_current_timezone()
    today = timezone.localdate()
    start = timezone.make_aware(datetime.combine(today, time.min), current_tz)
    end = start + timedelta(days=1)
    return start, end, today


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
    """Return a human-friendly label for ``transaction``'s charger."""

    charger = transaction.charger
    if charger is None:
        return "Unknown charger"
    for attr in ("display_name", "name", "charger_id"):
        value = getattr(charger, attr, "")
        if value:
            return str(value)
    return str(charger)


@shared_task(name="apps.ocpp.tasks.send_daily_session_report")
def send_daily_session_report() -> int:
    """Send a summary of today's OCPP sessions when email is available."""

    if not mailer.can_send_email():
        logger.info("Skipping OCPP session report: email not configured")
        return 0

    if not is_celery_enabled():
        logger.info("Skipping OCPP session report: celery feature disabled")
        return 0

    recipients = session_report_recipients()
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
        stop_local = timezone.localtime(transaction.stop_time) if transaction.stop_time else None
        duration = _format_duration(stop_local - start_local if stop_local else None)
        account = transaction.account.name if transaction.account else "N/A"
        connector_letter = Charger.connector_letter_from_value(transaction.connector_id)
        connector = f"Connector {connector_letter}" if connector_letter else None
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
        lines.append("   Start: " f"{start_local.strftime('%H:%M:%S %Z')}")
        if stop_local:
            lines.append("   Stop: " f"{stop_local.strftime('%H:%M:%S %Z')} ({duration})")
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
        mailer.send(subject, body, recipients, getattr(settings, "DEFAULT_FROM_EMAIL", None))

    logger.info("Sent OCPP session report for %s to %s", today.isoformat(), ", ".join(recipients))
    return len(transactions)


@shared_task(name="apps.ocpp.tasks.send_offline_charge_point_notifications", rate_limit="12/h")
def send_offline_charge_point_notifications() -> int:
    """Send offline notifications for charge points that stay offline."""

    if not mailer.can_send_email():
        logger.info("Skipping offline charge point notifications: email not configured")
        return 0
    if not is_celery_enabled():
        logger.info("Skipping offline charge point notifications: celery disabled")
        return 0

    now = timezone.now()
    cutoff = now - OFFLINE_NOTIFICATION_GRACE
    candidates = (
        Charger.objects.annotate(
            last_activity=Greatest(
                Coalesce("last_status_timestamp", "last_heartbeat"),
                Coalesce("last_heartbeat", "last_status_timestamp"),
            ),
        )
        .filter(last_activity__isnull=False, last_activity__lt=cutoff)
        .select_related("user", "group", "location")
        .order_by("charger_id", "connector_id")
    )

    charger_ids = {charger.charger_id for charger in candidates}
    station_map: dict[str, Charger] = {}
    if charger_ids:
        station_map = {
            station.charger_id: station
            for station in Charger.objects.filter(charger_id__in=charger_ids, connector_id__isnull=True)
        }

    def _station_for(charger: Charger) -> Charger:
        if charger.connector_id is None:
            return charger
        return station_map.get(charger.charger_id, charger)

    def _offline_notification_source(charger: Charger) -> Charger:
        if charger.email_when_offline or charger.maintenance_email:
            return charger
        return _station_for(charger)

    def _email_when_offline_value(charger: Charger, station: Charger) -> bool:
        if charger.email_when_offline:
            return True
        if station.pk != charger.pk:
            return bool(station.email_when_offline)
        return False

    sources: dict[int, Charger] = {}
    for charger in candidates:
        station = _station_for(charger)
        source = _offline_notification_source(charger)
        if not _email_when_offline_value(source, station):
            continue
        if source.last_seen and source.last_seen > cutoff:
            continue
        if source.pk is not None and source.pk not in sources:
            sources[source.pk] = source

    sent = 0
    for source in sources.values():
        last_sent = source.offline_notification_sent_at
        if last_sent and (now - last_sent) < OFFLINE_NOTIFICATION_COOLDOWN:
            continue
        recipient = resolve_offline_notification_recipient(source)
        if not recipient:
            logger.info("Skipping offline notification for %s: no recipient resolved", source.charger_id)
            continue

        subject = f"Charge point offline: {source.charger_id}"
        last_seen = source.last_seen
        grace_minutes = int(OFFLINE_NOTIFICATION_GRACE.total_seconds() // 60)
        message = [
            (
                f"Charge point {source.charger_id} has been offline for more than "
                f"{grace_minutes} minutes."
            ),
        ]
        if source.display_name:
            message.append(f"Display name: {source.display_name}")
        if source.location:
            message.append(f"Location: {source.location}")
        if last_seen:
            message.append(f"Last seen: {timezone.localtime(last_seen).isoformat()}")
        try:
            mailer.send(subject=subject, message="\n".join(message), recipient_list=[recipient])
        except Exception as exc:
            logger.exception("Failed to send offline notification for %s: %s", source.charger_id, exc)
            continue
        source.offline_notification_sent_at = now
        source.save(update_fields=["offline_notification_sent_at"])
        sent += 1

    logger.info("Sent %s offline charge point notifications", sent)
    return sent
