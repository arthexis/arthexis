from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.ocpp.models.location import Location

from .models import Charger, MeterValue, Transaction


def _parse_remote_datetime(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        timestamp = value
    else:
        timestamp = parse_datetime(str(value))
    if not timestamp:
        return None
    if timezone.is_naive(timestamp):
        timestamp = timezone.make_aware(timestamp, timezone.get_current_timezone())
    return timestamp


def _to_decimal(value) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def serialize_charger_for_network(charger: Charger) -> dict[str, object]:
    simple_fields = [
        "authorization_policy",
        "display_name",
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

    datetime_fields = [
        "firmware_timestamp",
        "last_heartbeat",
        "availability_state_updated_at",
        "availability_requested_at",
        "availability_request_status_at",
        "diagnostics_timestamp",
        "last_status_timestamp",
        "last_online_at",
    ]

    data: dict[str, object] = {
        "charger_id": charger.charger_id,
        "connector_id": charger.connector_id,
        "allow_remote": charger.allow_remote,
        "export_transactions": charger.export_transactions,
        "last_meter_values": charger.last_meter_values or {},
        "last_charging_limit": charger.last_charging_limit or {},
    }

    data["language"] = charger.language_code()

    for field in simple_fields:
        data[field] = getattr(charger, field)

    for field in datetime_fields:
        value = getattr(charger, field)
        data[field] = value.isoformat() if value else None

    data["last_charging_limit_at"] = (
        charger.last_charging_limit_at.isoformat()
        if charger.last_charging_limit_at
        else None
    )

    if charger.location:
        location = charger.location
        data["location"] = {
            "name": location.name,
            "latitude": location.latitude,
            "longitude": location.longitude,
            "zone": location.zone,
            "contract_type": location.contract_type,
        }

    return data


def apply_remote_charger_payload(
    node,
    payload: Mapping,
    *,
    create_missing: bool = True,
) -> Charger | None:
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

    charger = (
        Charger.objects.filter(charger_id=serial, connector_id=connector_value)
        .select_related("location")
        .first()
    )

    if charger is None and not create_missing:
        return None

    if charger is None:
        charger = Charger.objects.create(
            charger_id=serial,
            connector_id=connector_value,
            node_origin=node,
        )

    location_obj = None
    location_payload = payload.get("location")
    if isinstance(location_payload, Mapping):
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
        "authorization_policy",
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
        "last_charging_limit_source",
        "last_charging_limit_is_grid_critical",
    ]

    for field in simple_fields:
        if field in payload:
            value = payload.get(field)
            if field in {"require_rfid", "public_display"} and value is None:
                value = False
            if field == "authorization_policy":
                normalized = str(value or "").strip().lower()
                if normalized in Charger.AuthorizationPolicy.values:
                    value = normalized
                elif normalized == "":
                    value = ""
                else:
                    value = charger.authorization_policy
            updates[field] = value
        else:
            updates[field] = getattr(charger, field)

    for field in datetime_fields:
        updates[field] = _parse_remote_datetime(payload.get(field))

    updates["last_meter_values"] = payload.get("last_meter_values") or {}
    updates["last_charging_limit"] = payload.get("last_charging_limit") or {}
    updates["last_charging_limit_at"] = _parse_remote_datetime(
        payload.get("last_charging_limit_at")
    )

    if location_obj is not None:
        updates["location"] = location_obj

    Charger.objects.filter(pk=charger.pk).update(**updates)
    charger.refresh_from_db()
    return charger


def _normalize_connector(value) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def sync_transactions_payload(payload: Mapping) -> int:
    if not isinstance(payload, Mapping):
        return 0

    chargers_map: dict[tuple[str, int | None], Charger] = {}
    charger_entries = payload.get("chargers", [])
    if isinstance(charger_entries, Iterable):
        for entry in charger_entries:
            if not isinstance(entry, Mapping):
                continue
            serial = Charger.normalize_serial(entry.get("charger_id"))
            if not serial or Charger.is_placeholder_serial(serial):
                continue
            connector_value = _normalize_connector(entry.get("connector_id"))
            charger = Charger.objects.filter(
                charger_id=serial, connector_id=connector_value
            ).first()
            if charger:
                chargers_map[(serial, connector_value)] = charger

    imported = 0
    transaction_entries = payload.get("transactions", [])
    if not isinstance(transaction_entries, Iterable):
        return 0

    for tx in transaction_entries:
        if not isinstance(tx, Mapping):
            continue
        serial = Charger.normalize_serial(tx.get("charger"))
        if not serial:
            continue
        connector_value = _normalize_connector(tx.get("connector_id"))

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
            "received_stop_time": _parse_remote_datetime(tx.get("received_stop_time")),
        }

        transaction, created = Transaction.objects.update_or_create(
            charger=charger,
            start_time=start_time,
            defaults=defaults,
        )

        if not created:
            transaction.meter_values.all().delete()

        meter_values = tx.get("meter_values", [])
        if isinstance(meter_values, Iterable):
            for mv in meter_values:
                if not isinstance(mv, Mapping):
                    continue
                timestamp = _parse_remote_datetime(mv.get("timestamp"))
                if timestamp is None:
                    continue
                connector_mv = _normalize_connector(mv.get("connector_id"))
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
