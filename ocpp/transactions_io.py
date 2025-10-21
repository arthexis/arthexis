from __future__ import annotations

from datetime import datetime
from typing import Iterable

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


def import_transactions(data: dict) -> int:
    """Import transactions from export data.

    Returns number of imported transactions.
    """
    charger_map: dict[str, Charger] = {}
    for item in data.get("chargers", []):
        try:
            serial = Charger.validate_serial(item.get("charger_id"))
        except ValidationError:
            continue
        connector_value = item.get("connector_id", None)
        if connector_value in ("", None):
            connector_value = None
        elif isinstance(connector_value, str):
            connector_value = int(connector_value)
        charger, _ = Charger.objects.get_or_create(
            charger_id=serial,
            defaults={
                "connector_id": connector_value,
                "require_rfid": item.get("require_rfid", False),
            },
        )
        charger_map[serial] = charger

    imported = 0
    for tx in data.get("transactions", []):
        serial = Charger.normalize_serial(tx.get("charger"))
        if not serial or Charger.is_placeholder_serial(serial):
            continue
        charger = charger_map.get(serial)
        if charger is None:
            try:
                charger, _ = Charger.objects.get_or_create(charger_id=serial)
            except ValidationError:
                continue
            charger_map[serial] = charger
        vid_value = tx.get("vid")
        vin_value = tx.get("vin")
        vid_text = str(vid_value).strip() if vid_value is not None else ""
        vin_text = str(vin_value).strip() if vin_value is not None else ""
        if not vid_text and vin_text:
            vid_text = vin_text
        transaction = Transaction.objects.create(
            charger=charger,
            account_id=tx.get("account"),
            rfid=tx.get("rfid", ""),
            vid=vid_text,
            vin=vin_text,
            meter_start=tx.get("meter_start"),
            meter_stop=tx.get("meter_stop"),
            voltage_start=tx.get("voltage_start"),
            voltage_stop=tx.get("voltage_stop"),
            current_import_start=tx.get("current_import_start"),
            current_import_stop=tx.get("current_import_stop"),
            current_offered_start=tx.get("current_offered_start"),
            current_offered_stop=tx.get("current_offered_stop"),
            temperature_start=tx.get("temperature_start"),
            temperature_stop=tx.get("temperature_stop"),
            soc_start=tx.get("soc_start"),
            soc_stop=tx.get("soc_stop"),
            start_time=_parse_dt(tx.get("start_time")),
            stop_time=_parse_dt(tx.get("stop_time")),
            received_start_time=_parse_dt(tx.get("received_start_time"))
            or _parse_dt(tx.get("start_time")),
            received_stop_time=_parse_dt(tx.get("received_stop_time"))
            or _parse_dt(tx.get("stop_time")),
        )
        for mv in tx.get("meter_values", []):
            connector_id = mv.get("connector_id")
            if isinstance(connector_id, str):
                connector_id = int(connector_id)
            MeterValue.objects.create(
                charger=charger,
                transaction=transaction,
                connector_id=connector_id,
                timestamp=_parse_dt(mv.get("timestamp")),
                context=mv.get("context", ""),
                energy=mv.get("energy"),
                voltage=mv.get("voltage"),
                current_import=mv.get("current_import"),
                current_offered=mv.get("current_offered"),
                temperature=mv.get("temperature"),
                soc=mv.get("soc"),
            )
        imported += 1
    return imported
