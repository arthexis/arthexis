from __future__ import annotations

from datetime import datetime
from typing import Iterable

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import Charger, Transaction, MeterReading


def export_transactions(
    start: datetime | None = None,
    end: datetime | None = None,
    chargers: Iterable[str] | None = None,
) -> dict:
    """Return transaction export data."""
    qs = (
        Transaction.objects.all()
        .select_related("charger")
        .prefetch_related("meter_readings")
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
                "number": charger.number,
                "config": charger.config,
                "require_rfid": charger.require_rfid,
            }
        )

    for tx in qs:
        data["transactions"].append(
            {
                "charger": tx.charger.charger_id if tx.charger else None,
                "account": tx.account_id,
                "rfid": tx.rfid,
                "vin": tx.vin,
                "meter_start": tx.meter_start,
                "meter_stop": tx.meter_stop,
                "start_time": tx.start_time.isoformat(),
                "stop_time": tx.stop_time.isoformat() if tx.stop_time else None,
                "meter_readings": [
                    {
                        "connector_id": mr.connector_id,
                        "timestamp": mr.timestamp.isoformat(),
                        "measurand": mr.measurand,
                        "value": str(mr.value),
                        "unit": mr.unit,
                    }
                    for mr in tx.meter_readings.all()
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
        charger, _ = Charger.objects.get_or_create(
            charger_id=item["charger_id"],
            defaults={
                "number": item.get("number", 1),
                "config": item.get("config", {}),
                "require_rfid": item.get("require_rfid", False),
            },
        )
        charger_map[item["charger_id"]] = charger

    imported = 0
    for tx in data.get("transactions", []):
        charger = charger_map.get(tx.get("charger"))
        transaction = Transaction.objects.create(
            charger=charger,
            account_id=tx.get("account"),
            rfid=tx.get("rfid", ""),
            vin=tx.get("vin", ""),
            meter_start=tx.get("meter_start"),
            meter_stop=tx.get("meter_stop"),
            start_time=_parse_dt(tx.get("start_time")),
            stop_time=_parse_dt(tx.get("stop_time")),
        )
        for mr in tx.get("meter_readings", []):
            MeterReading.objects.create(
                charger=charger,
                transaction=transaction,
                connector_id=mr.get("connector_id"),
                timestamp=_parse_dt(mr.get("timestamp")),
                measurand=mr.get("measurand", ""),
                value=mr.get("value"),
                unit=mr.get("unit", ""),
            )
        imported += 1
    return imported
