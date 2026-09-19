"""Retained OCPP transaction and meter persistence services."""

from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.utils import timezone

from apps.energy.models import CustomerAccount
from apps.ocpp.models import Charger, Connector, MeterValue, OcppTransaction


def _prefetched_transactions(charger: Charger) -> list[OcppTransaction] | None:
    return getattr(charger, "_prefetched_transactions", None)


def current_transaction(charger: Charger) -> OcppTransaction | None:
    """Return the most recently started active transaction for one charger."""
    prefetched = _prefetched_transactions(charger)
    if prefetched is not None:
        return next(
            (
                transaction
                for transaction in prefetched
                if transaction.stopped_at is None
            ),
            None,
        )
    return charger.transactions.active().recent().first()


def last_transaction(charger: Charger) -> OcppTransaction | None:
    """Return the most recently started transaction for one charger."""
    prefetched = _prefetched_transactions(charger)
    if prefetched is not None:
        return prefetched[0] if prefetched else None
    return charger.transactions.recent().first()


def last_completed_transaction(charger: Charger) -> OcppTransaction | None:
    """Return the most recently started completed transaction for one charger."""
    prefetched = _prefetched_transactions(charger)
    if prefetched is not None:
        return next(
            (
                transaction
                for transaction in prefetched
                if transaction.stopped_at is not None
            ),
            None,
        )
    return charger.transactions.completed().recent().first()


def start_transaction(
    *,
    charger: Charger,
    connector_id: int,
    id_tag: str,
    account: CustomerAccount | None,
    meter_start: object | None,
    timestamp: object | None,
) -> OcppTransaction:
    """Persist an authorized OCPP 1.6 transaction start."""
    connector, _ = Connector.objects.get_or_create(charger=charger, number=connector_id)
    transaction = OcppTransaction.objects.create(
        charger=charger,
        connector=connector,
        account=account,
        id_tag=id_tag,
        remote_id=f"{charger.pk}-{timezone.now().timestamp()}",
        started_at=_parse_timestamp(timestamp),
        meter_start=_parse_decimal(meter_start),
    )
    return transaction


def stop_transaction(
    *,
    transaction_id: int,
    charger: Charger,
    meter_stop: object | None,
    timestamp: object | None,
) -> OcppTransaction:
    """Persist a transaction stop only for the connected charger."""
    transaction = OcppTransaction.objects.get(
        pk=transaction_id,
        charger=charger,
        stopped_at__isnull=True,
    )
    transaction.stopped_at = _parse_timestamp(timestamp)
    transaction.meter_stop = _parse_decimal(meter_stop)
    transaction.energy_kwh = _meter_delta_kwh(
        transaction.meter_start, transaction.meter_stop
    )
    transaction.save(update_fields=("stopped_at", "meter_stop", "energy_kwh"))
    return transaction


def record_meter_values(
    *, transaction_id: int, charger: Charger, meter_values: object
) -> int:
    """Persist all sampled OCPP 1.6 meter values for one live transaction."""
    if not isinstance(meter_values, list):
        raise ValueError("meterValue must be a list")
    transaction = OcppTransaction.objects.get(pk=transaction_id, charger=charger)
    count = _record_meter_values(transaction=transaction, meter_values=meter_values)
    _refresh_meter_value_energy(transaction)
    return count


def record_v201_transaction_event(
    *,
    charger: Charger,
    event_type: str,
    transaction_id: str,
    id_token: str,
    evse_id: int | None,
    connector_id: int | None,
    timestamp: object | None,
) -> OcppTransaction:
    """Persist a retained OCPP 2.0.1 transaction lifecycle event."""
    connector = _connector(charger, evse_id, connector_id)
    transaction, _ = OcppTransaction.objects.get_or_create(
        charger=charger,
        remote_id=transaction_id,
        defaults={
            "connector": connector,
            "id_tag": id_token[:20],
            "started_at": _parse_timestamp(timestamp),
        },
    )
    if event_type == "Ended" and transaction.stopped_at is None:
        transaction.stopped_at = _parse_timestamp(timestamp)
        transaction.energy_kwh = _meter_value_delta_kwh(transaction)
        transaction.save(update_fields=("stopped_at", "energy_kwh"))
    return transaction


def record_v201_meter_values(
    *, charger: Charger, transaction_id: str, meter_values: object
) -> int:
    """Persist OCPP 2.0.1 meter values identified by their remote transaction ID."""
    transaction = OcppTransaction.objects.get(charger=charger, remote_id=transaction_id)
    count = _record_meter_values(transaction=transaction, meter_values=meter_values)
    _refresh_meter_value_energy(transaction)
    return count


def _record_meter_values(*, transaction: OcppTransaction, meter_values: object) -> int:
    """Create sampled-value records for either retained OCPP protocol version."""
    records: list[MeterValue] = []
    for meter_value in meter_values:
        if not isinstance(meter_value, dict):
            raise ValueError("Each meter value must be an object")
        sampled_values = meter_value.get("sampledValue")
        if not isinstance(sampled_values, list):
            raise ValueError("sampledValue must be a list")
        sampled_at = _parse_timestamp(meter_value.get("timestamp"))
        for sampled_value in sampled_values:
            if not isinstance(sampled_value, dict):
                raise ValueError("Each sampled value must be an object")
            unit, multiplier = _unit_of_measure(sampled_value)
            records.append(
                MeterValue(
                    transaction=transaction,
                    sampled_at=sampled_at,
                    value=_parse_decimal(sampled_value.get("value")),
                    measurand=str(
                        sampled_value.get("measurand", "Energy.Active.Import.Register")
                    ),
                    unit=unit,
                    multiplier=multiplier,
                )
            )
    MeterValue.objects.bulk_create(records)
    return len(records)


def _refresh_meter_value_energy(transaction: OcppTransaction) -> None:
    energy_kwh = _meter_value_delta_kwh(transaction)
    if energy_kwh is None or transaction.energy_kwh == energy_kwh:
        return
    transaction.energy_kwh = energy_kwh
    transaction.save(update_fields=("energy_kwh",))


def _meter_delta_kwh(
    meter_start: Decimal | None, meter_stop: Decimal | None
) -> Decimal | None:
    if meter_start is None or meter_stop is None or meter_stop < meter_start:
        return None
    return (meter_stop - meter_start) / Decimal("1000")


def _meter_value_delta_kwh(transaction: OcppTransaction) -> Decimal | None:
    readings = transaction.meter_values.filter(
        measurand="Energy.Active.Import.Register"
    )
    values = [_as_kwh(reading) for reading in readings]
    if len(values) < 2 or any(value is None for value in values):
        return None
    minimum = min(value for value in values if value is not None)
    maximum = max(value for value in values if value is not None)
    return maximum - minimum if maximum >= minimum else None


def _as_kwh(meter_value: MeterValue) -> Decimal | None:
    value = meter_value.value * (Decimal(10) ** meter_value.multiplier)
    if meter_value.unit == "Wh":
        return value / Decimal("1000")
    if meter_value.unit == "kWh":
        return value
    return None


def _unit_of_measure(sampled_value: dict[str, object]) -> tuple[str, int]:
    unit = sampled_value.get("unit", "Wh")
    multiplier = sampled_value.get("multiplier", 0)
    unit_of_measure = sampled_value.get("unitOfMeasure")
    if isinstance(unit_of_measure, dict):
        unit = unit_of_measure.get("unit", unit)
        multiplier = unit_of_measure.get("multiplier", multiplier)
    if not isinstance(unit, str) or not unit:
        raise ValueError("Meter unit is invalid")
    if not isinstance(multiplier, int):
        raise ValueError("Meter multiplier is invalid")
    return unit, multiplier


def _connector(
    charger: Charger, evse_id: int | None, connector_id: int | None
) -> Connector | None:
    if evse_id is None or connector_id is None:
        return None
    connector, _ = Connector.objects.get_or_create(
        charger=charger,
        number=(evse_id * 1000) + connector_id,
    )
    return connector


def _parse_decimal(value: object | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError("Meter value must be numeric") from error


def _parse_timestamp(value: object | None) -> datetime:
    if value is None:
        return timezone.now()
    if not isinstance(value, str):
        raise ValueError("Timestamp must be text")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("Timestamp is invalid") from error
