"""Retained OCPP transaction and meter persistence services."""

import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256

from django.db import transaction as db_transaction
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
                if (
                    transaction.recovery_state
                    == OcppTransaction.RecoveryState.ACTIVE
                    and transaction.stopped_at is None
                )
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
    started_at = _parse_timestamp(timestamp)
    transaction = OcppTransaction.objects.create(
        charger=charger,
        connector=connector,
        account=account,
        id_tag=id_tag,
        remote_id=f"{charger.pk}-{timezone.now().timestamp()}",
        started_at=started_at,
        last_activity_at=started_at,
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
    stopped_at = _parse_timestamp(timestamp)
    transaction.stopped_at = stopped_at
    transaction.last_activity_at = _latest_activity(
        transaction.last_activity_at,
        stopped_at,
    )
    transaction.recovery_state = OcppTransaction.RecoveryState.COMPLETED
    transaction.meter_stop = _parse_decimal(meter_stop)
    transaction.energy_kwh = _meter_delta_kwh(
        transaction.meter_start, transaction.meter_stop
    )
    transaction.save(
        update_fields=(
            "stopped_at",
            "last_activity_at",
            "recovery_state",
            "meter_stop",
            "energy_kwh",
        )
    )
    return transaction


def record_meter_values(
    *, transaction_id: int, charger: Charger, meter_values: object
) -> int:
    """Persist all sampled OCPP 1.6 meter values for one live transaction."""
    if not isinstance(meter_values, list):
        raise ValueError("meterValue must be a list")
    transaction = OcppTransaction.objects.get(pk=transaction_id, charger=charger)
    count, last_activity = _record_meter_values(
        transaction=transaction,
        meter_values=meter_values,
    )
    _touch_transaction_activity(transaction, last_activity)
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
    live_evidence: bool = True,
) -> OcppTransaction:
    """Persist a retained OCPP 2.0.1 transaction lifecycle event."""
    connector = _connector(charger, evse_id, connector_id)
    occurred_at = _parse_timestamp(timestamp)
    transaction, _ = OcppTransaction.objects.get_or_create(
        charger=charger,
        remote_id=transaction_id,
        defaults={
            "connector": connector,
            "id_tag": id_token[:20],
            "started_at": occurred_at,
            "last_activity_at": occurred_at,
        },
    )
    update_fields: list[str] = []
    previous_activity = transaction.last_activity_at
    latest_activity = _latest_activity(previous_activity, occurred_at)
    if latest_activity != previous_activity:
        transaction.last_activity_at = latest_activity
        update_fields.append("last_activity_at")
    if (
        live_evidence
        and event_type != "Ended"
        and transaction.recovery_state == OcppTransaction.RecoveryState.UNRESOLVED
        and occurred_at > previous_activity
    ):
        transaction.recovery_state = OcppTransaction.RecoveryState.ACTIVE
        update_fields.append("recovery_state")
    if (
        not live_evidence
        and transaction.stopped_at is None
        and transaction.recovery_state == OcppTransaction.RecoveryState.ACTIVE
    ):
        transaction.recovery_state = OcppTransaction.RecoveryState.UNRESOLVED
        update_fields.append("recovery_state")
    if event_type == "Ended" and transaction.stopped_at is None:
        transaction.stopped_at = occurred_at
        transaction.recovery_state = OcppTransaction.RecoveryState.COMPLETED
        transaction.energy_kwh = _meter_value_delta_kwh(transaction)
        update_fields.extend(("stopped_at", "recovery_state", "energy_kwh"))
    if update_fields:
        transaction.save(update_fields=tuple(dict.fromkeys(update_fields)))
    return transaction


def record_v201_meter_values(
    *, charger: Charger, transaction_id: str, meter_values: object
) -> int:
    """Persist OCPP 2.0.1 meter values identified by their remote transaction ID."""
    transaction = OcppTransaction.objects.get(charger=charger, remote_id=transaction_id)
    count, last_activity = _record_meter_values(
        transaction=transaction,
        meter_values=meter_values,
    )
    _touch_transaction_activity(transaction, last_activity)
    return count


def _record_meter_values(
    *, transaction: OcppTransaction, meter_values: object
) -> tuple[int, datetime | None]:
    """Create sampled-value records and return the newest retained activity time."""
    records: list[MeterValue] = []
    latest_activity: datetime | None = None
    for meter_value in meter_values:
        if not isinstance(meter_value, dict):
            raise ValueError("Each meter value must be an object")
        sampled_values = meter_value.get("sampledValue")
        if not isinstance(sampled_values, list):
            raise ValueError("sampledValue must be a list")
        sampled_at = _parse_timestamp(meter_value.get("timestamp"))
        latest_activity = (
            sampled_at
            if latest_activity is None
            else max(latest_activity, sampled_at)
        )
        for sampled_value in sampled_values:
            if not isinstance(sampled_value, dict):
                raise ValueError("Each sampled value must be an object")
            unit, multiplier = _unit_of_measure(sampled_value)
            value = _parse_decimal(sampled_value.get("value"))
            measurand = str(
                sampled_value.get("measurand", "Energy.Active.Import.Register")
            )
            fingerprint = _meter_sample_fingerprint(
                sampled_at=sampled_at,
                value=value,
                measurand=measurand,
                unit=unit,
                multiplier=multiplier,
            )
            records.append(
                MeterValue(
                    transaction=transaction,
                    sampled_at=sampled_at,
                    value=value,
                    measurand=measurand,
                    unit=unit,
                    multiplier=multiplier,
                    source_fingerprint=fingerprint,
                )
            )

    fingerprints = {record.source_fingerprint for record in records}
    existing = set(
        transaction.meter_values.filter(
            source_fingerprint__in=fingerprints
        ).values_list("source_fingerprint", flat=True)
    )
    unique_records: list[MeterValue] = []
    seen = set(existing)
    for record in records:
        if record.source_fingerprint in seen:
            continue
        seen.add(record.source_fingerprint)
        unique_records.append(record)
    MeterValue.objects.bulk_create(unique_records, ignore_conflicts=True)
    persisted = set(
        transaction.meter_values.filter(
            source_fingerprint__in=fingerprints
        ).values_list("source_fingerprint", flat=True)
    )
    return len(persisted - existing), latest_activity


def recompute_transaction_energy(transaction_id: int) -> OcppTransaction:
    """Recompute derived transaction energy from authoritative retained samples."""
    transaction = OcppTransaction.objects.get(pk=transaction_id)
    energy_kwh = _meter_value_delta_kwh(transaction)
    if energy_kwh is None or transaction.energy_kwh == energy_kwh:
        return transaction
    transaction.energy_kwh = energy_kwh
    transaction.save(update_fields=("energy_kwh",))
    return transaction


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


def mark_transaction_unresolved(
    transaction: OcppTransaction,
    *,
    observed_at: datetime | None = None,
) -> OcppTransaction:
    """Preserve an open transaction while marking its live state as uncertain."""
    if transaction.stopped_at is not None:
        raise ValueError("Completed transactions cannot become unresolved.")
    transaction.recovery_state = OcppTransaction.RecoveryState.UNRESOLVED
    update_fields = ["recovery_state"]
    if observed_at is not None:
        transaction.last_activity_at = _latest_activity(
            transaction.last_activity_at,
            observed_at,
        )
        update_fields.append("last_activity_at")
    transaction.save(update_fields=tuple(update_fields))
    return transaction


def _touch_transaction_activity(
    transaction: OcppTransaction,
    occurred_at: datetime | None,
) -> None:
    if occurred_at is None:
        return
    current = transaction.last_activity_at
    if occurred_at <= current:
        return
    update_fields = ["last_activity_at"]
    transaction.last_activity_at = occurred_at
    if transaction.recovery_state == OcppTransaction.RecoveryState.UNRESOLVED:
        transaction.recovery_state = OcppTransaction.RecoveryState.ACTIVE
        update_fields.append("recovery_state")
    transaction.save(update_fields=tuple(update_fields))


def _latest_activity(current: datetime, candidate: datetime) -> datetime:
    return max(current, candidate)


def _meter_sample_fingerprint(
    *,
    sampled_at: datetime,
    value: Decimal,
    measurand: str,
    unit: str,
    multiplier: int,
) -> str:
    material = json.dumps(
        {
            "sampled_at": sampled_at.isoformat(),
            "value": str(value.normalize()),
            "measurand": measurand,
            "unit": unit,
            "multiplier": multiplier,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return sha256(material).hexdigest()


@db_transaction.atomic
def reconcile_connector_status(
    *,
    charger: Charger,
    connector_number: int,
    status: str,
    observed_at: object | None = None,
) -> Connector:
    """Persist fresh connector evidence and mark contradictory open sessions unresolved."""
    connector, _ = Connector.objects.update_or_create(
        charger=charger,
        number=connector_number,
        defaults={"status": status},
    )
    if status != "Available":
        return connector

    evidence_at = _parse_timestamp(observed_at)
    for selected in (
        OcppTransaction.objects.active()
        .filter(charger=charger, connector=connector)
        .select_for_update()
    ):
        if evidence_at <= selected.last_activity_at:
            continue
        mark_transaction_unresolved(selected, observed_at=evidence_at)
    return connector
