"""Persistence helpers for CSMS action handlers."""

from __future__ import annotations

from django.utils import timezone

from apps.ocpp.models import Charger


def update_status_notification_records(
    *,
    charger_id: str,
    connector_value: int | str | None,
    primary_charger: Charger,
    aggregate_charger: Charger | None,
    update_kwargs: dict[str, object],
) -> None:
    """Persist status fields for aggregate and connector charger records."""

    target = aggregate_charger
    if connector_value is not None:
        target = Charger.objects.filter(
            charger_id=charger_id,
            connector_id=connector_value,
        ).first()
    if not target and not primary_charger.connector_id:
        target = primary_charger
    if target:
        for field, value in update_kwargs.items():
            setattr(target, field, value)
        if target.pk:
            Charger.objects.filter(pk=target.pk).update(**update_kwargs)

    connector = (
        Charger.objects.filter(charger_id=charger_id, connector_id=connector_value)
        .exclude(pk=primary_charger.pk)
        .first()
    )
    if connector:
        for field, value in update_kwargs.items():
            setattr(connector, field, value)
        connector.save(update_fields=list(update_kwargs.keys()))


def persist_legacy_meter_values(*, charger_pk: int, payload: dict) -> None:
    """Persist latest MeterValues payload on the charger record."""

    Charger.objects.filter(pk=charger_pk).update(last_meter_values=payload)


def update_availability_state_records(
    *,
    charger_id: str,
    connector_value: int | None,
    state: str,
    timestamp,
) -> list[Charger]:
    """Persist availability state for matching charger records and return touched rows."""

    filters: dict[str, object] = {"charger_id": charger_id}
    if connector_value is None:
        filters["connector_id__isnull"] = True
    else:
        filters["connector_id"] = connector_value
    updates = {
        "availability_state": state,
        "availability_state_updated_at": timestamp or timezone.now(),
    }
    targets = list(Charger.objects.filter(**filters))
    if not targets:
        return []
    Charger.objects.filter(pk__in=[target.pk for target in targets]).update(**updates)
    for target in targets:
        for field, value in updates.items():
            setattr(target, field, value)
    return targets
