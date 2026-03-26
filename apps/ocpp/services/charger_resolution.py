"""Helpers to resolve charger records for CSMS persistence workflows."""

from __future__ import annotations

from apps.ocpp.models import Charger


def resolve_charger_target(
    *,
    charger: Charger | None,
    aggregate_charger: Charger | None,
    charger_id: str | None,
    connector_id: int | None,
) -> Charger | None:
    """Resolve an existing charger row or create one when possible."""

    target = aggregate_charger or charger
    if target is not None:
        return target
    if not charger_id:
        return None

    target = (
        Charger.objects.filter(charger_id=charger_id, connector_id=connector_id).first()
        or Charger.objects.filter(charger_id=charger_id, connector_id__isnull=True).first()
    )
    if target is not None:
        return target

    target, _created = Charger.objects.get_or_create(
        charger_id=charger_id,
        connector_id=connector_id,
    )
    return target
