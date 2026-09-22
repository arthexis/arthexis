"""Celery adapters for the durable event outbox."""

from celery import shared_task

from apps.events.dispatch import dispatch_pending_events_batch
from apps.events.models import EventEnvelope
from apps.events.registry import dispatch_to_subscribers


def enqueue_event(envelope: EventEnvelope):
    """Best-effort broker handoff used by the SQL dispatcher."""
    return process_event.delay(str(envelope.event_id))


@shared_task(name="events.process")
def process_event(event_id: str) -> int:
    """Load one durable event from SQL and invoke registered consumers."""
    envelope = EventEnvelope.objects.get(event_id=event_id)
    return dispatch_to_subscribers(envelope)


@shared_task(name="events.dispatch_pending")
def dispatch_pending_events() -> dict[str, int]:
    """Dispatch one recoverable batch of pending outbox events."""
    return dispatch_pending_events_batch()
