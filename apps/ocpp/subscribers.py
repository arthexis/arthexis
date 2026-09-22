"""OCPP event subscribers for secondary durable processing."""

from apps.events.models import EventEnvelope
from apps.events.registry import subscribe
from apps.ocpp.domain.sessions import recompute_transaction_energy


def register_subscribers() -> None:
    subscribe("ocpp.meter_values.received", process_meter_values_received)


def process_meter_values_received(envelope: EventEnvelope) -> None:
    """Recompute transaction energy when a retained meter event names a transaction."""
    transaction_id = envelope.payload.get("transaction_id")
    if transaction_id is None:
        return
    if isinstance(transaction_id, bool) or not isinstance(transaction_id, int):
        raise ValueError("transaction_id must be an integer")
    recompute_transaction_energy(transaction_id)
