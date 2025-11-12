from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Iterable

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import Charger, MeterValue, Transaction
from core.models import Location


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


def serialize_charger_for_network(
    charger: Charger,
    *,
    forwarded_messages: Iterable[str] | None = None,
) -> dict[str, object]:
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
    }

    for field in simple_fields:
        data[field] = getattr(charger, field)

    for field in datetime_fields:
        value = getattr(charger, field)
        data[field] = value.isoformat() if value else None

    if charger.location:
        location = charger.location
        data["location"] = {
            "name": location.name,
            "latitude": location.latitude,
            "longitude": location.longitude,
            "zone": location.zone,
            "contract_type": location.contract_type,
        }

    if forwarded_messages is not None:
        cleaned = [str(value) for value in forwarded_messages if value]
        data["forwarded_messages"] = cleaned

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

    charger = Charger.objects.filter(
        charger_id=serial, connector_id=connector_value
    ).select_related("location").first()

    if charger is None and not create_missing:
        return None

    if charger is None:
        charger = Charger.objects.create(
            charger_id=serial,
            connector_id=connector_value,
            node_origin=node,
            forwarded_to=None,
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
        "forwarded_to": None,
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
        if field in payload:
            value = payload.get(field)
            if field in {"require_rfid", "public_display"} and value is None:
                value = False
            updates[field] = value
        else:
            updates[field] = getattr(charger, field)

    for field in datetime_fields:
        updates[field] = _parse_remote_datetime(payload.get(field))

    updates["last_meter_values"] = payload.get("last_meter_values") or {}

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
            "received_start_time": _parse_remote_datetime(tx.get("received_start_time")),
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


def serialize_transactions_for_forwarding(
    transactions: Iterable[Transaction],
) -> list[dict[str, object]]:
    serialized: list[dict[str, object]] = []
    for tx in transactions:
        serialized.append(
            {
                "charger": tx.charger.charger_id if tx.charger else None,
                "connector_id": tx.connector_id,
                "account": tx.account_id,
                "rfid": tx.rfid,
                "vid": tx.vehicle_identifier,
                "vin": tx.vin,
                "meter_start": tx.meter_start,
                "meter_stop": tx.meter_stop,
                "voltage_start": str(tx.voltage_start)
                if tx.voltage_start is not None
                else None,
                "voltage_stop": str(tx.voltage_stop)
                if tx.voltage_stop is not None
                else None,
                "current_import_start": str(tx.current_import_start)
                if tx.current_import_start is not None
                else None,
                "current_import_stop": str(tx.current_import_stop)
                if tx.current_import_stop is not None
                else None,
                "current_offered_start": str(tx.current_offered_start)
                if tx.current_offered_start is not None
                else None,
                "current_offered_stop": str(tx.current_offered_stop)
                if tx.current_offered_stop is not None
                else None,
                "temperature_start": str(tx.temperature_start)
                if tx.temperature_start is not None
                else None,
                "temperature_stop": str(tx.temperature_stop)
                if tx.temperature_stop is not None
                else None,
                "soc_start": str(tx.soc_start) if tx.soc_start is not None else None,
                "soc_stop": str(tx.soc_stop) if tx.soc_stop is not None else None,
                "start_time": tx.start_time.isoformat() if tx.start_time else None,
                "stop_time": tx.stop_time.isoformat() if tx.stop_time else None,
                "received_start_time": tx.received_start_time.isoformat()
                if tx.received_start_time
                else None,
                "received_stop_time": tx.received_stop_time.isoformat()
                if tx.received_stop_time
                else None,
                "meter_values": [
                    {
                        "connector_id": mv.connector_id,
                        "timestamp": mv.timestamp.isoformat(),
                        "context": mv.context,
                        "energy": str(mv.energy) if mv.energy is not None else None,
                        "voltage": str(mv.voltage) if mv.voltage is not None else None,
                        "current_import": str(mv.current_import)
                        if mv.current_import is not None
                        else None,
                        "current_offered": str(mv.current_offered)
                        if mv.current_offered is not None
                        else None,
                        "temperature": str(mv.temperature)
                        if mv.temperature is not None
                        else None,
                        "soc": str(mv.soc) if mv.soc is not None else None,
                    }
                    for mv in tx.meter_values.all()
                ],
            }
        )
    return serialized


def newest_transaction_timestamp(transactions: Iterable[Transaction]) -> datetime | None:
    latest: datetime | None = None
    for tx in transactions:
        if tx.start_time and (
            latest is None or tx.start_time > latest
        ):
            latest = tx.start_time
    return latest
