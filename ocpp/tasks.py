import base64
import json
import logging
import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from asgiref.sync import async_to_sync
from celery import shared_task
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.db.models import Q
import requests
from requests import RequestException
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

from core import mailer
from nodes.models import Node

from . import store
from .models import Charger, Location, MeterValue, Transaction

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


def _parse_remote_datetime(value):
    if not value:
        return None
    dt = parse_datetime(value)
    if dt is None:
        return None
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


def _to_decimal(value):
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _apply_remote_charger_payload(node, payload: dict) -> Charger | None:
    serial = Charger.normalize_serial(payload.get("charger_id"))
    if not serial or Charger.is_placeholder_serial(serial):
        return None

    connector = payload.get("connector_id")
    if connector in (None, ""):
        connector_value = None
    elif isinstance(connector, int):
        connector_value = connector
    else:
        try:
            connector_value = int(str(connector))
        except (TypeError, ValueError):
            connector_value = None

    charger = Charger.objects.filter(
        charger_id=serial, connector_id=connector_value
    ).first()
    if not charger:
        return None

    location_payload = payload.get("location")
    location_obj = None
    if isinstance(location_payload, dict):
        name = location_payload.get("name")
        if name:
            location_obj, _ = Location.objects.get_or_create(name=name)
            for field in ("latitude", "longitude", "zone", "contract_type"):
                setattr(location_obj, field, location_payload.get(field))
            location_obj.save()

    datetime_fields = [
        "firmware_timestamp",
        "last_heartbeat",
        "availability_state_updated_at",
        "availability_requested_at",
        "availability_request_status_at",
        "diagnostics_timestamp",
        "last_status_timestamp",
    ]

    updates: dict[str, object] = {
        "node_origin": node,
        "allow_remote": bool(payload.get("allow_remote", False)),
        "export_transactions": bool(payload.get("export_transactions", False)),
        "last_online_at": timezone.now(),
    }

    simple_fields = [
        "display_name",
        "language",
        "public_display",
        "require_rfid",
        "firmware_status",
        "firmware_status_info",
        "last_status",
        "last_error_code",
        "last_status_vendor_info",
        "availability_state",
        "availability_requested_state",
        "availability_request_status",
        "availability_request_details",
        "temperature",
        "temperature_unit",
        "diagnostics_status",
        "diagnostics_location",
    ]

    for field in simple_fields:
        updates[field] = payload.get(field)

    if location_obj is not None:
        updates["location"] = location_obj

    for field in datetime_fields:
        updates[field] = _parse_remote_datetime(payload.get(field))

    updates["last_meter_values"] = payload.get("last_meter_values") or {}

    Charger.objects.filter(pk=charger.pk).update(**updates)
    charger.refresh_from_db()
    return charger


def _sync_transactions_payload(payload: dict) -> int:
    if not isinstance(payload, dict):
        return 0

    chargers_map: dict[tuple[str, int | None], Charger] = {}
    for entry in payload.get("chargers", []):
        serial = Charger.normalize_serial(entry.get("charger_id"))
        if not serial or Charger.is_placeholder_serial(serial):
            continue
        connector = entry.get("connector_id")
        if connector in (None, ""):
            connector_value = None
        elif isinstance(connector, int):
            connector_value = connector
        else:
            try:
                connector_value = int(str(connector))
            except (TypeError, ValueError):
                connector_value = None
        charger = Charger.objects.filter(
            charger_id=serial, connector_id=connector_value
        ).first()
        if charger:
            chargers_map[(serial, connector_value)] = charger

    imported = 0
    for tx in payload.get("transactions", []):
        if not isinstance(tx, dict):
            continue
        serial = Charger.normalize_serial(tx.get("charger"))
        if not serial:
            continue
        connector = tx.get("connector_id")
        if connector in (None, ""):
            connector_value = None
        elif isinstance(connector, int):
            connector_value = connector
        else:
            try:
                connector_value = int(str(connector))
            except (TypeError, ValueError):
                connector_value = None

        charger = chargers_map.get((serial, connector_value))
        if charger is None:
            charger = chargers_map.get((serial, None))
        if charger is None:
            continue

        start_time = _parse_remote_datetime(tx.get("start_time"))
        if start_time is None:
            continue

        defaults = {
            "connector_id": connector_value,
            "account_id": tx.get("account"),
            "rfid": tx.get("rfid", ""),
            "vid": tx.get("vid", ""),
            "vin": tx.get("vin", ""),
            "meter_start": tx.get("meter_start"),
            "meter_stop": tx.get("meter_stop"),
            "voltage_start": _to_decimal(tx.get("voltage_start")),
            "voltage_stop": _to_decimal(tx.get("voltage_stop")),
            "current_import_start": _to_decimal(tx.get("current_import_start")),
            "current_import_stop": _to_decimal(tx.get("current_import_stop")),
            "current_offered_start": _to_decimal(tx.get("current_offered_start")),
            "current_offered_stop": _to_decimal(tx.get("current_offered_stop")),
            "temperature_start": _to_decimal(tx.get("temperature_start")),
            "temperature_stop": _to_decimal(tx.get("temperature_stop")),
            "soc_start": _to_decimal(tx.get("soc_start")),
            "soc_stop": _to_decimal(tx.get("soc_stop")),
            "stop_time": _parse_remote_datetime(tx.get("stop_time")),
            "received_start_time": _parse_remote_datetime(
                tx.get("received_start_time")
            ),
            "received_stop_time": _parse_remote_datetime(
                tx.get("received_stop_time")
            ),
        }

        transaction, _ = Transaction.objects.update_or_create(
            charger=charger,
            start_time=start_time,
            defaults=defaults,
        )
        transaction.meter_values.all().delete()
        for mv in tx.get("meter_values", []):
            if not isinstance(mv, dict):
                continue
            timestamp = _parse_remote_datetime(mv.get("timestamp"))
            if timestamp is None:
                continue
            connector_mv = mv.get("connector_id")
            if connector_mv in (None, ""):
                connector_mv = None
            elif isinstance(connector_mv, str):
                try:
                    connector_mv = int(connector_mv)
                except (TypeError, ValueError):
                    connector_mv = None
            MeterValue.objects.create(
                charger=charger,
                transaction=transaction,
                connector_id=connector_mv,
                timestamp=timestamp,
                context=mv.get("context", ""),
                energy=_to_decimal(mv.get("energy")),
                voltage=_to_decimal(mv.get("voltage")),
                current_import=_to_decimal(mv.get("current_import")),
                current_offered=_to_decimal(mv.get("current_offered")),
                temperature=_to_decimal(mv.get("temperature")),
                soc=_to_decimal(mv.get("soc")),
            )
        imported += 1

    return imported


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
def sync_remote_chargers() -> int:
    """Synchronize remote charger metadata and transactions."""

    local = Node.get_local()
    if not local:
        logger.debug("Remote sync skipped: local node not registered")
        return 0

    private_key = local.get_private_key()
    if private_key is None:
        logger.warning("Remote sync skipped: missing local node private key")
        return 0

    chargers = (
        Charger.objects.filter(export_transactions=True)
        .exclude(node_origin__isnull=True)
        .select_related("node_origin")
    )
    if local.pk:
        chargers = chargers.exclude(node_origin=local)

    grouped: dict[Node, list[Charger]] = {}
    for charger in chargers:
        origin = charger.node_origin
        if not origin:
            continue
        grouped.setdefault(origin, []).append(charger)

    if not grouped:
        return 0

    synced = 0
    for node, node_chargers in grouped.items():
        payload = {
            "requester": str(local.uuid),
            "include_transactions": True,
            "chargers": [],
        }
        for charger in node_chargers:
            payload["chargers"].append(
                {
                    "charger_id": charger.charger_id,
                    "connector_id": charger.connector_id,
                    "since": charger.last_online_at.isoformat()
                    if charger.last_online_at
                    else None,
                }
            )
        payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        signature = _sign_payload(payload_json, private_key)
        headers = {"Content-Type": "application/json"}
        if signature:
            headers["X-Signature"] = signature

        url = next(node.iter_remote_urls("/nodes/network/chargers/"), "")
        if not url:
            logger.warning("No reachable host found for %s when syncing chargers", node)
            continue
        try:
            response = requests.post(url, data=payload_json, headers=headers, timeout=5)
        except RequestException as exc:
            logger.warning("Failed to sync chargers from %s: %s", node, exc)
            continue

        if not response.ok:
            logger.warning(
                "Sync request to %s returned %s", node, response.status_code
            )
            continue

        try:
            data = response.json()
        except ValueError:
            logger.warning("Invalid JSON payload received from %s", node)
            continue

        chargers_payload = data.get("chargers", [])
        for entry in chargers_payload:
            charger = _apply_remote_charger_payload(node, entry)
            if charger:
                synced += 1

        transactions_payload = data.get("transactions")
        if transactions_payload:
            imported = _sync_transactions_payload(transactions_payload)
            if imported:
                logger.info(
                    "Imported %s transaction(s) from node %s", imported, node
                )

    return synced


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
