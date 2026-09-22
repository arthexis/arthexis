"""Read-only charger state snapshots for operator surfaces."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from django.db.models import Prefetch
from django.db.models.query import QuerySet

from apps.ocpp.domain.sessions import (
    current_transaction,
    last_completed_transaction,
)
from apps.ocpp.models import Charger, OcppTransaction
from apps.ocpp.services.presence import connection_is_live


@dataclass(frozen=True)
class ChargerSnapshot:
    """A captured operational summary without protocol payload contents."""

    identity: str
    enabled: bool
    state: str
    configured_protocol: str | None
    connection_state: str
    connector_states: tuple[str, ...]
    active_transactions: int
    current_transaction_id: str | None
    current_transaction_started: datetime | None
    last_transaction_id: str | None
    last_transaction_stopped: datetime | None
    energy_kwh: Decimal | None
    unresolved_sessions: int
    last_contact: datetime | None


def _transactions(charger: Charger) -> list[OcppTransaction]:
    prefetched = getattr(charger, "_prefetched_transactions", None)
    if prefetched is not None:
        return prefetched
    transactions = list(charger.transactions.recent())
    charger._prefetched_transactions = transactions
    return transactions


def _state(charger: Charger, current: OcppTransaction | None) -> str:
    if not charger.active:
        return "disabled"
    if not connection_is_live(charger):
        return "offline"
    return "charging" if current is not None else "idle"


def snapshot_charger(charger: Charger) -> ChargerSnapshot:
    """Summarize one charger from persisted, retained OCPP records."""
    transactions = _transactions(charger)
    current = current_transaction(charger)
    last_completed = last_completed_transaction(charger)
    completed = [
        transaction
        for transaction in transactions
        if transaction.stopped_at is not None
    ]
    energy_values = [
        transaction.energy_kwh
        for transaction in completed
        if transaction.energy_kwh is not None
    ]
    return ChargerSnapshot(
        identity=charger.identity,
        enabled=charger.active,
        state=_state(charger, current),
        configured_protocol=(
            charger.station_model.preferred_protocol if charger.station_model else None
        ),
        connection_state="connected" if connection_is_live(charger) else "disconnected",
        connector_states=tuple(
            f"{connector.number}:{connector.status}"
            for connector in sorted(
                charger.connectors.all(),
                key=lambda connector: connector.number,
            )
        ),
        active_transactions=sum(
            transaction.stopped_at is None for transaction in transactions
        ),
        current_transaction_id=current.remote_id if current else None,
        current_transaction_started=current.started_at if current else None,
        last_transaction_id=(last_completed.remote_id if last_completed else None),
        last_transaction_stopped=(
            last_completed.stopped_at if last_completed else None
        ),
        energy_kwh=sum(energy_values, Decimal("0")) if energy_values else None,
        unresolved_sessions=sum(
            transaction.energy_kwh is None for transaction in completed
        ),
        last_contact=charger.connected_at,
    )


def snapshot_chargers(
    chargers: QuerySet[Charger] | None = None,
) -> list[ChargerSnapshot]:
    """Return snapshots for selected chargers in identity order."""
    chargers = (
        (chargers if chargers is not None else Charger.objects.all())
        .select_related("station_model", "connection")
        .prefetch_related(
            "connectors",
            Prefetch(
                "transactions",
                queryset=OcppTransaction.objects.recent(),
                to_attr="_prefetched_transactions",
            ),
        )
        .order_by("identity")
    )
    return [snapshot_charger(charger) for charger in chargers]
