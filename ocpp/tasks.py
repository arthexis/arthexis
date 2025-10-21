import logging
from datetime import date, datetime, time, timedelta
from pathlib import Path

from celery import shared_task
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.db.models import Q

from core import mailer
from nodes.models import Node

from .models import MeterValue, Transaction

logger = logging.getLogger(__name__)


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


def _resolve_report_window() -> tuple[datetime, datetime, date]:
    """Return the start/end datetimes for today's reporting window."""

    current_tz = timezone.get_current_timezone()
    today = timezone.localdate()
    start = timezone.make_aware(datetime.combine(today, time.min), current_tz)
    end = start + timedelta(days=1)
    return start, end, today


def _session_report_recipients() -> list[str]:
    """Return the list of recipients for the daily session report."""

    User = get_user_model()
    recipients = list(
        User.objects.filter(is_superuser=True)
        .exclude(email="")
        .values_list("email", flat=True)
    )
    if recipients:
        return recipients

    fallback = getattr(settings, "DEFAULT_FROM_EMAIL", "").strip()
    return [fallback] if fallback else []


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

    celery_lock = Path(settings.BASE_DIR) / "locks" / "celery.lck"
    if not celery_lock.exists():
        logger.info("Skipping OCPP session report: celery feature disabled")
        return 0

    recipients = _session_report_recipients()
    if not recipients:
        logger.info("Skipping OCPP session report: no recipients found")
        return 0

    start, end, today = _resolve_report_window()
    transactions = list(
        Transaction.objects.filter(start_time__gte=start, start_time__lt=end)
        .select_related("charger", "account")
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
        connector = (
            f"Connector {transaction.connector_id}" if transaction.connector_id else None
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
