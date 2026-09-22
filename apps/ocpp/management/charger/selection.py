"""Charger selection rules shared by the operator command surface."""

from django.core.management.base import CommandError
from django.db.models.query import QuerySet

from apps.ocpp.models import Charger

FLEET_FILTERS = frozenset(
    {
        "enabled",
        "disabled",
        "connected",
        "disconnected",
        "charging",
        "idle",
        "unresolved",
        "historical",
    }
)


def select_chargers(
    *,
    identities: list[str],
    select_all: bool,
    filters: tuple[str, ...] = (),
) -> QuerySet[Charger]:
    """Return named chargers or the whole fleet for an explicit all selection."""
    if select_all and identities:
        raise CommandError("Use --all by itself or name chargers with --charger.")
    normalized = [identity.strip() for identity in identities if identity.strip()]
    if len(normalized) != len(set(normalized)):
        raise CommandError("Each charger may be selected only once.")
    chargers = Charger.objects.all()
    selected = chargers.filter(identity__in=normalized) if normalized else chargers
    if normalized:
        found = set(selected.values_list("identity", flat=True))
        missing = sorted(set(normalized) - found)
        if missing:
            raise CommandError(f"Unknown charger: {missing[0]}")
    unknown_filters = set(filters) - FLEET_FILTERS
    if unknown_filters:
        raise CommandError(f"Unknown fleet filter: {sorted(unknown_filters)[0]}")
    for name in filters:
        selected = getattr(selected, name)()
    return selected
