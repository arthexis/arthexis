import base64
import json
import logging
import uuid
from datetime import date, datetime, time, timedelta
from pathlib import Path

from asgiref.sync import async_to_sync
from celery import shared_task
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone
import requests
from requests import RequestException
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

from core import mailer
from nodes.models import Node

from . import store
from .models import Charger, MeterValue, Transaction
from .network import (
    newest_transaction_timestamp,
    serialize_charger_for_network,
    serialize_transactions_for_forwarding,
)

logger = logging.getLogger(__name__)


def _sign_payload(payload_json: str, private_key) -> str | None:
    if not private_key:
        return None
    try:
        signature = private_key.sign(
            payload_json.encode(),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except Exception:
        return None
    return base64.b64encode(signature).decode()


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
    """Push local charge point sessions to configured upstream nodes."""

    local = Node.get_local()
    if not local:
        logger.debug("Forwarding skipped: local node not registered")
        return 0

    private_key = local.get_private_key()
    if private_key is None:
        logger.warning("Forwarding skipped: missing local node private key")
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
    if not chargers:
        return 0

    grouped: dict[Node, list[Charger]] = {}
    for charger in chargers:
        target = charger.forwarded_to
        if not target:
            continue
        if local.pk and target.pk == local.pk:
            continue
        grouped.setdefault(target, []).append(charger)

    if not grouped:
        return 0

    forwarded_total = 0

    for node, node_chargers in grouped.items():
        if not node_chargers:
            continue

        initializing = [ch for ch in node_chargers if ch.forwarding_watermark is None]
        charger_by_pk = {ch.pk: ch for ch in node_chargers}
        transactions_map: dict[int, list[Transaction]] = {}

        for charger in node_chargers:
            watermark = charger.forwarding_watermark
            if watermark is None:
                continue
            tx_queryset = (
                Transaction.objects.filter(charger=charger, start_time__gt=watermark)
                .select_related("charger")
                .prefetch_related("meter_values")
                .order_by("start_time")
            )
            txs = list(tx_queryset)
            if txs:
                transactions_map[charger.pk] = txs

        transaction_payload = {"chargers": [], "transactions": []}
        for charger_pk, txs in transactions_map.items():
            charger = charger_by_pk[charger_pk]
            transaction_payload["chargers"].append(
                {
                    "charger_id": charger.charger_id,
                    "connector_id": charger.connector_id,
                    "require_rfid": charger.require_rfid,
                }
            )
            transaction_payload["transactions"].extend(
                serialize_transactions_for_forwarding(txs)
            )

        payload = {
            "requester": str(local.uuid),
            "requester_mac": local.mac_address,
            "requester_public_key": local.public_key,
            "chargers": [serialize_charger_for_network(ch) for ch in initializing],
        }

        has_transactions = bool(transaction_payload["transactions"])
        if has_transactions or payload["chargers"]:
            payload["transactions"] = transaction_payload
        else:
            continue

        payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        signature = _sign_payload(payload_json, private_key)
        headers = {"Content-Type": "application/json"}
        if signature:
            headers["X-Signature"] = signature

        success = False
        attempted = False
        for url in node.iter_remote_urls("/nodes/network/chargers/forward/"):
            if not url:
                continue

            attempted = True
            try:
                response = requests.post(
                    url, data=payload_json, headers=headers, timeout=5
                )
            except RequestException as exc:
                logger.warning("Failed to forward chargers to %s: %s", node, exc)
                continue

            if not response.ok:
                logger.warning(
                    "Forwarding request to %s via %s returned %s",
                    node,
                    url,
                    response.status_code,
                )
                continue

            try:
                data = response.json()
            except ValueError:
                logger.warning("Invalid JSON payload received from %s", node)
                continue

            if data.get("status") != "ok":
                detail = data.get("detail") if isinstance(data, dict) else None
                logger.warning(
                    "Forwarding rejected by %s via %s: %s",
                    node,
                    url,
                    detail or response.text or "Remote node rejected the request.",
                )
                continue

            success = True
            break

        if not success:
            if not attempted:
                logger.warning(
                    "No reachable host found for %s when forwarding chargers", node
                )
            continue

        updates: dict[int, datetime] = {}
        now = timezone.now()
        for charger in initializing:
            updates[charger.pk] = now
        for charger_pk, txs in transactions_map.items():
            latest = newest_transaction_timestamp(txs)
            if latest:
                updates[charger_pk] = latest

        for pk, timestamp in updates.items():
            Charger.objects.filter(pk=pk).update(forwarding_watermark=timestamp)

        forwarded_total += len(transaction_payload["transactions"])

    return forwarded_total


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
