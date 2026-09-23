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
    cleared_sessions: int
    last_cleared_transaction_id: str | null
    last_recovery_cleared_at: datetime | null
    last_recovery_clear_reason: str
    unresolved_energy_sessions: int
    authority_cutover_at: datetime | None
    historical_sessions: int
    historical_open_sessions: int
    historical_oldest_started: datetime | None
    historical_latest_activity: datetime | None
    last_contact: datetime | None


def _transactions(charger: Charger) -> list[OcppTransaction]:
    prefetched = getattr(charger, "_prefetched_transactions", None)
    if prefetched is not None:
        return prefetched
    return list(charger.transactions.recent())


def _state(
    charger: Charger,
    current: OcppTransaction | None,
    unresolved_count: int,
) -> str:
    if not charger.active:
        return "disabled"
    if not connection_is_live(charger):
        return "offline"
    if unresolved_count:
        return "unresolved"
    return "charging" if current is not None else "idle"


def snapshot_charger(charger: Charger) -> ChargerSnapshot:
    """Summarize one charger from persisted, retained OCPP records."""
    transactions = _transactions(charger)
    current = current_transaction(charger)
    last_completed = last_completed_transaction(charger)
    unresolved = [
        transaction
        for transaction in transactions
        if (
            not transaction.historical
            and transaction.recovery_state
            == OcppTransaction.RecoveryState.UNRESOLVED
            and transaction.stopped_at is None
        )
    ]
    cleared = [
        transaction
        for transaction in transactions
        if (
            not transaction.historical
            and transaction.recovery_state
            == OcppTransaction.RecoveryState.CLEARED
            and transaction.stopped_at is None
        )
    ]
    latest_cleared = max(
        cleared,
        key=lambda transaction: (
            transaction.recovery_cleared_at or transaction.last_activity_at,
            transaction.pk,
        ),
        default=None,
    )
    historical = [
        transaction for transaction in transactions if transaction.historical
    ]
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
        state=_state(charger, current, len(unresolved)),
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
            not transaction.historical
            and transaction.recovery_state == OcppTransaction.RecoveryState.ACTIVE
            and transaction.stopped_at is None
            for transaction in transactions
        ),
        current_transaction_id=current.remote_id if current else None,
        current_transaction_started=current.started_at if current else None,
        last_transaction_id=(last_completed.remote_id if last_completed else None),
        last_transaction_stopped=(
            last_completed.stopped_at if last_completed else None
        ),
        energy_kwh=sum(energy_values, Decimal("0")) if energy_values else None,
        unresolved_sessions=len(unresolved),
        cleared_sessions=len(cleared),
        last_cleared_transaction_id=(
            latest_cleared.remote_id if latest_cleared is not None else None
        ),
        last_recovery_cleared_at=(
            latest_cleared.recovery_cleared_at if latest_cleared is not None else None
        ),
        last_recovery_clear_reason=(
            latest_cleared.recovery_clear_reason if latest_cleared is not None else ""
        ),
        unresolved_energy_sessions=sum(
            transaction.energy_kwh is None for transaction in completed
        ),
        authority_cutover_at=charger.authority_cutover_at,
        historical_sessions=len(historical),
        historical_open_sessions=sum(
            transaction.stopped_at is None for transaction in historical
        ),
        historical_oldest_started=(
            min(transaction.started_at for transaction in historical)
            if historical
            else None
        ),
        historical_latest_activity=(
            max(transaction.last_activity_at for transaction in historical)
            if historical
            else None
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
