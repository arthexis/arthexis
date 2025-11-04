import json
import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Iterable

from asgiref.sync import async_to_sync
from celery import shared_task
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone
from urllib.parse import quote, urlsplit, urlunsplit
from websocket import WebSocketException, create_connection

from core import mailer
from nodes.models import Node

from . import store
from .models import Charger, MeterValue, Transaction
logger = logging.getLogger(__name__)


@dataclass
class ForwardingSession:
    """Active websocket forwarding session for a charge point."""

    charger_pk: int
    node_id: int | None
    url: str
    connection: object
    connected_at: datetime

    @property
    def is_connected(self) -> bool:
        return bool(getattr(self.connection, "connected", False))


_FORWARDING_SESSIONS: dict[int, ForwardingSession] = {}

def _candidate_forwarding_urls(node: Node, charger: Charger) -> Iterable[str]:
    """Yield websocket URLs suitable for forwarding ``charger`` via ``node``."""

    charger_id = (charger.charger_id or "").strip()
    if not charger_id:
        return []

    encoded_id = quote(charger_id, safe="")
    for base in node.iter_remote_urls("/"):
        if not base:
            continue
        parsed = urlsplit(base)
        if parsed.scheme not in {"http", "https"}:
            continue
        scheme = "wss" if parsed.scheme == "https" else "ws"
        base_path = parsed.path.rstrip("/")
        for prefix in ("", "/ws"):
            path = f"{base_path}{prefix}/{encoded_id}".replace("//", "/")
            if not path.startswith("/"):
                path = f"/{path}"
            yield urlunsplit((scheme, parsed.netloc, path, "", ""))


def _close_forwarding_session(session: ForwardingSession) -> None:
    """Close the websocket connection associated with ``session`` if open."""

    connection = session.connection
    if connection is None:
        return
    try:
        connection.close()
    except Exception:  # pragma: no cover - best effort close
        pass


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


@shared_task
def schedule_daily_charge_point_configuration_checks() -> int:
    """Dispatch configuration requests for eligible charge points."""

    charger_ids = list(
        Charger.objects.filter(connector_id__isnull=True).values_list("pk", flat=True)
    )
    if not charger_ids:
        logger.debug("No eligible charge points available for configuration check")
        return 0

    scheduled = 0
    for charger_pk in charger_ids:
        check_charge_point_configuration.delay(charger_pk)
        scheduled += 1
    logger.info(
        "Scheduled configuration checks for %s charge point(s)", scheduled
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


@shared_task
def push_forwarded_charge_points() -> int:
    """Ensure websocket connections exist for forwarded charge points."""

    local = Node.get_local()
    if not local:
        logger.debug("Forwarding skipped: local node not registered")
        return 0

    chargers_qs = (
        Charger.objects.filter(export_transactions=True, forwarded_to__isnull=False)
        .select_related("forwarded_to", "node_origin")
        .order_by("pk")
    )

    node_filter = Q(node_origin__isnull=True)
    if local.pk:
        node_filter |= Q(node_origin=local)

    chargers = list(chargers_qs.filter(node_filter))
    active_ids = {charger.pk for charger in chargers}

    # Close sessions that no longer map to active forwarded chargers.
    for pk in list(_FORWARDING_SESSIONS.keys()):
        if pk not in active_ids:
            session = _FORWARDING_SESSIONS.pop(pk)
            _close_forwarding_session(session)

    if not chargers:
        return 0

    connected = 0

    for charger in chargers:
        target = charger.forwarded_to
        if not target:
            continue
        if local.pk and target.pk == local.pk:
            continue

        existing = _FORWARDING_SESSIONS.get(charger.pk)
        if existing and existing.node_id == getattr(target, "pk", None):
            if existing.is_connected:
                continue
            _close_forwarding_session(existing)
            _FORWARDING_SESSIONS.pop(charger.pk, None)

        for url in _candidate_forwarding_urls(target, charger):
            try:
                connection = create_connection(
                    url,
                    timeout=5,
                    subprotocols=["ocpp1.6"],
                )
            except (WebSocketException, OSError) as exc:
                logger.warning(
                    "Websocket forwarding connection to %s via %s failed: %s",
                    target,
                    url,
                    exc,
                )
                continue

            session = ForwardingSession(
                charger_pk=charger.pk,
                node_id=getattr(target, "pk", None),
                url=url,
                connection=connection,
                connected_at=timezone.now(),
            )
            _FORWARDING_SESSIONS[charger.pk] = session
            Charger.objects.filter(pk=charger.pk).update(
                forwarding_watermark=session.connected_at
            )
            connected += 1
            logger.info(
                "Established forwarding websocket for charger %s to %s via %s",
                charger.charger_id,
                target,
                url,
            )
            break
        else:
            logger.warning(
                "Unable to establish forwarding websocket for charger %s",
                charger.charger_id or charger.pk,
            )

    return connected


# Backwards compatibility alias for legacy schedules
sync_remote_chargers = push_forwarded_charge_points


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
