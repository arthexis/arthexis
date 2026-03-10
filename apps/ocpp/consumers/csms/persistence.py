"""Persistence helpers for CSMS action handlers."""

from __future__ import annotations

from django.urls import NoReverseMatch, reverse
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


def _ocpp_security_event_key(*, charger_id: str, connector_value: int | str | None) -> str:
    """Return a deterministic security event key for a charger status stream."""

    connector_label = "aggregate" if connector_value is None else str(connector_value)
    return f"ocpp-charger-{charger_id}-{connector_label}-error"


def sync_charger_error_security_event(
    *,
    charger_id: str,
    connector_value: int | str | None,
    status: str,
    error_code: str,
    status_timestamp,
) -> None:
    """Persist OCPP fault/error state into ``SecurityAlertEvent`` records.

    Connected charger StatusNotification payloads call this helper to keep the
    ops security widget aligned with active charger errors.
    """

    from apps.ops.models import SecurityAlertEvent

    normalized_status = (status or "").strip()
    normalized_error_code = (error_code or "").strip()
    normalized_error_casefold = normalized_error_code.casefold()
    is_error = normalized_status.casefold() == "faulted" or (
        bool(normalized_error_code) and normalized_error_casefold != "noerror"
    )

    key = _ocpp_security_event_key(
        charger_id=charger_id,
        connector_value=connector_value,
    )
    existing = SecurityAlertEvent.objects.filter(key=key).first()

    if not is_error:
        if existing and existing.is_active:
            existing.is_active = False
            existing.save(update_fields=["is_active", "updated_at"])
        return

    connector_label = "aggregate" if connector_value is None else str(connector_value)
    status_label = normalized_status or "Unknown"
    detail = (
        f"charger_id={charger_id}; connector={connector_label}; "
        f"status={status_label}; error_code={normalized_error_code or 'None'}"
    )
    message = f"OCPP charger {charger_id} connector {connector_label} reported {status_label}."
    event_timestamp = status_timestamp or timezone.now()

    if existing and existing.is_active and existing.last_occurred_at == event_timestamp:
        existing.severity = "error"
        existing.message = message
        existing.detail = detail
        existing.remediation_url = existing.remediation_url or "/admin/ocpp/charger/"
        existing.save(
            update_fields=["severity", "message", "detail", "remediation_url", "updated_at"]
        )
        return

    try:
        remediation_url = reverse("admin:ocpp_charger_changelist")
    except NoReverseMatch:
        remediation_url = "/admin/ocpp/charger/"

    SecurityAlertEvent.record_occurrence(
        key=key,
        severity="error",
        message=message,
        detail=detail,
        remediation_url=remediation_url,
        occurred_at=event_timestamp,
    )


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
