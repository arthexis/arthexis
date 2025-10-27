from __future__ import annotations

import base64
import json
import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Iterable

import requests
from requests import RequestException

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

from .models import Charger, Location, Transaction

logger = logging.getLogger(__name__)

SNAPSHOT_PATH = "/ocpp/remote/chargers/"
SNAPSHOT_TIMEOUT = 10


def _render_payload(local_node) -> bytes:
    payload = {
        "requester": str(local_node.uuid),
        "timestamp": timezone.now().isoformat(),
    }
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()


def _sign_body(private_key, body: bytes) -> str:
    signature = private_key.sign(body, padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(signature).decode()


def fetch_remote_snapshot(node, local_node, private_key, *, timeout: int = SNAPSHOT_TIMEOUT):
    """Return remote charger snapshot data and an optional error message."""

    body = _render_payload(local_node)
    headers = {
        "Content-Type": "application/json",
        "X-Signature": _sign_body(private_key, body),
    }
    last_error = ""

    for url in node.iter_remote_urls(SNAPSHOT_PATH):
        try:
            response = requests.post(url, data=body, headers=headers, timeout=timeout)
        except RequestException as exc:
            last_error = str(exc)
            logger.warning("Failed to fetch chargers from %s: %s", node, exc)
            continue
        if response.status_code != 200:
            last_error = f"{response.status_code} {response.reason}"
            logger.warning(
                "Remote node %s responded with %s during charger discovery",
                node,
                last_error,
            )
            continue
        try:
            return response.json(), None
        except ValueError:
            last_error = "Invalid JSON response"
            logger.warning(
                "Remote node %s returned invalid JSON during charger discovery",
                node,
            )
            continue

    return None, last_error or "Unable to reach remote node."


def _parse_datetime(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if value in (None, ""):
        return None
    parsed = parse_datetime(str(value))
    if parsed is None:
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _normalize_connector(value) -> int | None:
    if value in (None, "", "null"):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_int(value):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_decimal(value):
    if value in (None, ""):
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _sync_transactions(charger: Charger, entries: Iterable[dict]) -> tuple[int, int]:
    created = 0
    updated = 0

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        start_time = _parse_datetime(entry.get("start_time"))
        if start_time is None:
            continue
        defaults = {
            "meter_start": _coerce_int(entry.get("meter_start")),
            "meter_stop": _coerce_int(entry.get("meter_stop")),
            "received_start_time": _parse_datetime(entry.get("received_start_time")),
            "received_stop_time": _parse_datetime(entry.get("received_stop_time")),
            "stop_time": _parse_datetime(entry.get("stop_time")),
            "rfid": entry.get("rfid", "") or "",
            "vid": entry.get("vid", "") or "",
            "vin": entry.get("vin", "") or "",
            "voltage_start": _coerce_decimal(entry.get("voltage_start")),
            "voltage_stop": _coerce_decimal(entry.get("voltage_stop")),
            "current_import_start": _coerce_decimal(entry.get("current_import_start")),
            "current_import_stop": _coerce_decimal(entry.get("current_import_stop")),
            "current_offered_start": _coerce_decimal(entry.get("current_offered_start")),
            "current_offered_stop": _coerce_decimal(entry.get("current_offered_stop")),
            "temperature_start": _coerce_decimal(entry.get("temperature_start")),
            "temperature_stop": _coerce_decimal(entry.get("temperature_stop")),
            "soc_start": _coerce_decimal(entry.get("soc_start")),
            "soc_stop": _coerce_decimal(entry.get("soc_stop")),
        }

        connector_id = _normalize_connector(entry.get("connector_id"))
        transaction, created_flag = Transaction.objects.update_or_create(
            charger=charger,
            start_time=start_time,
            connector_id=connector_id,
            defaults=defaults,
        )
        if created_flag:
            created += 1
        else:
            updated += 1

    return created, updated


def apply_remote_snapshot(node, payload):
    """Create or update chargers for ``node`` using snapshot ``payload``."""

    chargers = payload.get("chargers")
    if not isinstance(chargers, list):
        return 0, 0, 0

    created = 0
    updated = 0
    transactions_synced = 0

    with transaction.atomic():
        for entry in chargers:
            if not isinstance(entry, dict):
                continue
            serial = str(entry.get("charger_id") or "").strip()
            if not serial:
                continue

            connector = _normalize_connector(entry.get("connector_id"))
            defaults: dict[str, object] = {
                "display_name": entry.get("display_name", "") or "",
                "public_display": bool(entry.get("public_display", True)),
                "require_rfid": bool(entry.get("require_rfid", False)),
                "last_path": entry.get("last_path", "") or "",
                "last_heartbeat": _parse_datetime(entry.get("last_heartbeat")),
                "last_meter_values": entry.get("last_meter_values") or {},
                "last_status": entry.get("last_status", "") or "",
                "last_status_timestamp": _parse_datetime(
                    entry.get("last_status_timestamp")
                ),
                "last_error_code": entry.get("last_error_code", "") or "",
                "last_status_vendor_info": entry.get("last_status_vendor_info"),
                "firmware_status": entry.get("firmware_status", "") or "",
                "firmware_status_info": entry.get("firmware_status_info", "") or "",
                "firmware_timestamp": _parse_datetime(entry.get("firmware_timestamp")),
                "availability_state": entry.get("availability_state", "") or "",
                "availability_state_updated_at": _parse_datetime(
                    entry.get("availability_state_updated_at")
                ),
                "availability_requested_state": entry.get(
                    "availability_requested_state", ""
                )
                or "",
                "availability_requested_at": _parse_datetime(
                    entry.get("availability_requested_at")
                ),
                "availability_request_status": entry.get(
                    "availability_request_status", ""
                )
                or "",
                "availability_request_status_at": _parse_datetime(
                    entry.get("availability_request_status_at")
                ),
                "availability_request_details": entry.get(
                    "availability_request_details", ""
                )
                or "",
                "diagnostics_status": entry.get("diagnostics_status"),
                "diagnostics_timestamp": _parse_datetime(
                    entry.get("diagnostics_timestamp")
                ),
                "diagnostics_location": entry.get("diagnostics_location"),
                "node_origin": node,
            }

            if not isinstance(defaults["last_meter_values"], dict):
                defaults["last_meter_values"] = {}
            if not isinstance(defaults["last_status_vendor_info"], dict):
                defaults["last_status_vendor_info"] = None

            language = entry.get("language")
            if isinstance(language, str) and language.strip():
                defaults["language"] = language

            location_name = entry.get("location")
            if isinstance(location_name, str):
                location_name = location_name.strip()
                if location_name:
                    location, _ = Location.objects.get_or_create(name=location_name)
                    defaults["location"] = location

            charger, created_flag = Charger.objects.update_or_create(
                charger_id=serial,
                connector_id=connector,
                defaults=defaults,
            )
            if created_flag:
                created += 1
            else:
                updated += 1

            tx_entries = entry.get("transactions") or []
            tx_created, tx_updated = _sync_transactions(charger, tx_entries)
            transactions_synced += tx_created + tx_updated

    return created, updated, transactions_synced
