from __future__ import annotations

from datetime import datetime
from typing import Iterable, Mapping

from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import Charger, Transaction, MeterValue


def export_transactions(
    start: datetime | None = None,
    end: datetime | None = None,
    chargers: Iterable[str] | None = None,
) -> dict:
    """Return transaction export data."""
    qs = (
        Transaction.objects.all()
        .select_related("charger")
        .prefetch_related("meter_values")
    )
    if start:
        qs = qs.filter(start_time__gte=start)
    if end:
        qs = qs.filter(start_time__lte=end)
    if chargers:
        qs = qs.filter(charger__charger_id__in=chargers)

    export_chargers = set(qs.values_list("charger__charger_id", flat=True))
    data = {"chargers": [], "transactions": []}

    for charger in Charger.objects.filter(charger_id__in=export_chargers):
        data["chargers"].append(
            {
                "charger_id": charger.charger_id,
                "connector_id": charger.connector_id,
                "require_rfid": charger.require_rfid,
            }
        )

    for tx in qs:
        data["transactions"].append(
            {
                "charger": tx.charger.charger_id if tx.charger else None,
                "account": tx.account_id,
                "rfid": tx.rfid,
                "vid": tx.vehicle_identifier,
                "vin": tx.vin,
                "ocpp_transaction_id": tx.ocpp_transaction_id,
                "meter_start": tx.meter_start,
                "meter_stop": tx.meter_stop,
                "voltage_start": tx.voltage_start,
                "voltage_stop": tx.voltage_stop,
                "current_import_start": tx.current_import_start,
                "current_import_stop": tx.current_import_stop,
                "current_offered_start": tx.current_offered_start,
                "current_offered_stop": tx.current_offered_stop,
                "temperature_start": tx.temperature_start,
                "temperature_stop": tx.temperature_stop,
                "soc_start": tx.soc_start,
                "soc_stop": tx.soc_stop,
                "start_time": tx.start_time.isoformat(),
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
                        "current_import": (
                            str(mv.current_import)
                            if mv.current_import is not None
                            else None
                        ),
                        "current_offered": (
                            str(mv.current_offered)
                            if mv.current_offered is not None
                            else None
                        ),
                        "temperature": (
                            str(mv.temperature) if mv.temperature is not None else None
                        ),
                        "soc": str(mv.soc) if mv.soc is not None else None,
                    }
                    for mv in tx.meter_values.all()
                ],
            }
        )
    return data


def _parse_dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    dt = parse_datetime(value)
    if dt is None:
        raise ValueError(f"Invalid datetime: {value}")
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt)
    return dt


def _normalize_connector(connector_value):
    if connector_value in ("", None):
        return None
    if isinstance(connector_value, str):
        try:
            return int(connector_value)
        except ValueError as exc:
            raise ValueError(f"Invalid connector id: {connector_value}") from exc
    return connector_value


def _build_charger_map(chargers: Iterable[Mapping]) -> dict[str, Charger]:
    charger_map: dict[str, Charger] = {}
    for item in chargers:
        try:
            serial = Charger.validate_serial(item.get("charger_id"))
            connector_value = _normalize_connector(item.get("connector_id"))
        except (ValidationError, ValueError):
            continue
        charger, _ = Charger.objects.get_or_create(
            charger_id=serial,
            defaults={
                "connector_id": connector_value,
                "require_rfid": item.get("require_rfid", False),
            },
        )
        charger_map[serial] = charger
    return charger_map


def _normalize_vehicle_identifiers(vid_value, vin_value) -> tuple[str, str]:
    vid_text = str(vid_value).strip() if vid_value is not None else ""
    vin_text = str(vin_value).strip() if vin_value is not None else ""
    if not vid_text and vin_text:
        vid_text = vin_text
    return vid_text, vin_text


def _normalize_meter_values(entries: Iterable[Mapping]) -> list[dict]:
    meter_values: list[dict] = []
    for mv in entries or []:
        try:
            connector_id = _normalize_connector(mv.get("connector_id"))
        except ValueError:
            continue
        try:
            timestamp = _parse_dt(mv.get("timestamp"))
        except ValueError:
            continue
        meter_values.append(
            {
                "connector_id": connector_id,
                "timestamp": timestamp,
                "context": mv.get("context", ""),
                "energy": mv.get("energy"),
                "voltage": mv.get("voltage"),
                "current_import": mv.get("current_import"),
                "current_offered": mv.get("current_offered"),
                "temperature": mv.get("temperature"),
                "soc": mv.get("soc"),
            }
        )
    return meter_values


def _normalize_transaction_entry(tx: Mapping, charger_map: dict[str, Charger]) -> dict:
    serial = Charger.normalize_serial(tx.get("charger"))
    if not serial or Charger.is_placeholder_serial(serial):
        raise ValidationError({"charger": "Invalid charger serial"})

    try:
        charger = charger_map.get(serial) or Charger.objects.get_or_create(charger_id=serial)[0]
        charger_map.setdefault(serial, charger)
    except ValidationError as exc:
        raise ValidationError(exc.messages) from exc

    vid_text, vin_text = _normalize_vehicle_identifiers(tx.get("vid"), tx.get("vin"))

    start_time = _parse_dt(tx.get("start_time"))
    stop_time = _parse_dt(tx.get("stop_time"))
    received_start_time = _parse_dt(tx.get("received_start_time")) or start_time
    received_stop_time = _parse_dt(tx.get("received_stop_time")) or stop_time

    return {
        "charger": charger,
        "transaction_fields": {
            "charger": charger,
            "account_id": tx.get("account"),
            "rfid": tx.get("rfid", ""),
            "vid": vid_text,
            "vin": vin_text,
            "ocpp_transaction_id": tx.get("ocpp_transaction_id", ""),
            "meter_start": tx.get("meter_start"),
            "meter_stop": tx.get("meter_stop"),
            "voltage_start": tx.get("voltage_start"),
            "voltage_stop": tx.get("voltage_stop"),
            "current_import_start": tx.get("current_import_start"),
            "current_import_stop": tx.get("current_import_stop"),
            "current_offered_start": tx.get("current_offered_start"),
            "current_offered_stop": tx.get("current_offered_stop"),
            "temperature_start": tx.get("temperature_start"),
            "temperature_stop": tx.get("temperature_stop"),
            "soc_start": tx.get("soc_start"),
            "soc_stop": tx.get("soc_stop"),
            "start_time": start_time,
            "stop_time": stop_time,
            "received_start_time": received_start_time,
            "received_stop_time": received_stop_time,
        },
        "meter_values": _normalize_meter_values(tx.get("meter_values", [])),
    }


def _persist_transactions_base(transactions: Iterable[dict]) -> Iterable[Transaction]:
    for tx in transactions:
        transaction = Transaction.objects.create(**tx["transaction_fields"])
        for mv in tx["meter_values"]:
            MeterValue.objects.create(transaction=transaction, charger=tx["charger"], **mv)
        yield transaction


def _persist_transactions(transactions: Iterable[dict]) -> int:
    return sum(1 for _ in _persist_transactions_base(transactions))


def _persist_transactions_with_objects(transactions: Iterable[dict]) -> list[Transaction]:
    return list(_persist_transactions_base(transactions))


def _is_duplicate_transaction(tx_fields: dict) -> bool:
    charger = tx_fields.get("charger")
    if charger is None:
        return False
    ocpp_transaction_id = tx_fields.get("ocpp_transaction_id") or ""
    if ocpp_transaction_id:
        if Transaction.objects.filter(
            charger=charger, ocpp_transaction_id=ocpp_transaction_id
        ).exists():
            return True
    start_time = tx_fields.get("start_time")
    if start_time:
        qs = Transaction.objects.filter(charger=charger, start_time=start_time)
        meter_start = tx_fields.get("meter_start")
        if meter_start is not None:
            qs = qs.filter(meter_start=meter_start)
        if qs.exists():
            return True
    return False


def _iter_normalized_transactions(
    data: dict, charger_map: dict[str, Charger]
) -> Iterable[dict]:
    for tx in data.get("transactions", []):
        if not isinstance(tx, Mapping):
            continue
        try:
            yield _normalize_transaction_entry(tx, charger_map)
        except (ValidationError, ValueError, TypeError):
            continue


def import_transactions(data: dict) -> int:
    """Import transactions from export data.

    Returns number of imported transactions.
    """

    charger_map = _build_charger_map(data.get("chargers", []))

    normalized_transactions = list(_iter_normalized_transactions(data, charger_map))
    return _persist_transactions(normalized_transactions)


def import_transactions_deduped(data: dict) -> tuple[int, int, list[Transaction]]:
    """Import transactions while skipping duplicates.

    Returns a tuple of (imported_count, skipped_count, created_transactions).
    """

    charger_map = _build_charger_map(data.get("chargers", []))

    skipped = 0
    normalized_transactions: list[dict] = []
    for normalized in _iter_normalized_transactions(data, charger_map):
        if _is_duplicate_transaction(normalized["transaction_fields"]):
            skipped += 1
            continue
        normalized_transactions.append(normalized)

    created = _persist_transactions_with_objects(normalized_transactions)
    return len(created), skipped, created
