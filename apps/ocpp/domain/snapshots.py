"""Read-only charger state snapshots for operator surfaces."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from django.db.models import Sum
from django.db.models.query import QuerySet

from apps.ocpp.models import Charger


@dataclass(frozen=True)
class ChargerSnapshot:
    """A captured operational summary without protocol payload contents."""

    identity: str
    configured_protocol: str | None
    connection_state: str
    connector_states: tuple[str, ...]
    active_transactions: int
    energy_kwh: Decimal | None
    unresolved_sessions: int
    last_contact: datetime | None


def snapshot_charger(charger: Charger) -> ChargerSnapshot:
    """Summarize one charger from persisted, retained OCPP records."""
    completed = charger.transactions.filter(stopped_at__isnull=False)
    aggregate = completed.aggregate(total=Sum("energy_kwh"))
    return ChargerSnapshot(
        identity=charger.identity,
        configured_protocol=(
            charger.station_model.preferred_protocol if charger.station_model else None
        ),
        connection_state="connected"
        if hasattr(charger, "connection")
        else "disconnected",
        connector_states=tuple(
            f"{connector.number}:{connector.status}"
            for connector in charger.connectors.order_by("number")
        ),
        active_transactions=charger.transactions.filter(
            stopped_at__isnull=True
        ).count(),
        energy_kwh=aggregate["total"],
        unresolved_sessions=completed.filter(energy_kwh__isnull=True).count(),
        last_contact=charger.connected_at,
    )


def snapshot_chargers(
    chargers: QuerySet[Charger] | None = None,
) -> list[ChargerSnapshot]:
    """Return snapshots for selected chargers in identity order."""
    chargers = (
        (chargers if chargers is not None else Charger.objects.all())
        .select_related("station_model", "connection")
        .order_by("identity")
    )
    return [snapshot_charger(charger) for charger in chargers]
