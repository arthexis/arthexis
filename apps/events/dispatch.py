"""Durable SQL-backed outbox dispatch for domain events."""

from collections.abc import Callable
from datetime import timedelta

from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from apps.events.models import EventEnvelope

DEFAULT_BATCH_SIZE = 100
STALE_DISPATCH_AFTER = timedelta(minutes=5)
_RETRY_DELAYS = (
    timedelta(minutes=1),
    timedelta(minutes=5),
    timedelta(minutes=15),
    timedelta(hours=1),
)

Enqueue = Callable[[EventEnvelope], object]


def _retry_delay(attempts: int) -> timedelta:
    index = min(max(attempts, 1), len(_RETRY_DELAYS)) - 1
    return _RETRY_DELAYS[index]


def _safe_error(error: Exception) -> str:
    return f"Broker handoff failed: {type(error).__name__}"


def claim_pending_events(
    *, limit: int = DEFAULT_BATCH_SIZE, now=None
) -> list[EventEnvelope]:
    """Claim due or stale events without holding locks during broker I/O."""
    current = now or timezone.now()
    stale_before = current - STALE_DISPATCH_AFTER
    due = Q(
        delivery_status__in=(
            EventEnvelope.DeliveryStatus.PENDING,
            EventEnvelope.DeliveryStatus.FAILED,
        ),
        next_delivery_at__isnull=True,
    ) | Q(
        delivery_status__in=(
            EventEnvelope.DeliveryStatus.PENDING,
            EventEnvelope.DeliveryStatus.FAILED,
        ),
        next_delivery_at__lte=current,
    )
    stale = Q(
        delivery_status=EventEnvelope.DeliveryStatus.DISPATCHING,
        dispatch_started_at__lte=stale_before,
    )

    with transaction.atomic():
        queryset = EventEnvelope.objects.filter(due | stale).order_by("created_at")
        if connection.features.has_select_for_update:
            queryset = queryset.select_for_update(
                skip_locked=connection.features.has_select_for_update_skip_locked
            )
        events = list(queryset[:limit])
        for envelope in events:
            envelope.delivery_status = EventEnvelope.DeliveryStatus.DISPATCHING
            envelope.delivery_attempts += 1
            envelope.dispatch_started_at = current
            envelope.next_delivery_at = None
            envelope.last_delivery_error = ""
            envelope.save(
                update_fields=(
                    "delivery_status",
                    "delivery_attempts",
                    "dispatch_started_at",
                    "next_delivery_at",
                    "last_delivery_error",
                )
            )
    return events


def mark_published(envelope: EventEnvelope, *, now=None) -> None:
    """Record that the broker accepted an event-processing task."""
    current = now or timezone.now()
    EventEnvelope.objects.filter(pk=envelope.pk).update(
        delivery_status=EventEnvelope.DeliveryStatus.PUBLISHED,
        published_at=current,
        dispatch_started_at=None,
        next_delivery_at=None,
        last_delivery_error="",
    )


def mark_failed(envelope: EventEnvelope, error: Exception, *, now=None) -> None:
    """Record a retryable broker handoff failure without losing the event."""
    current = now or timezone.now()
    EventEnvelope.objects.filter(pk=envelope.pk).update(
        delivery_status=EventEnvelope.DeliveryStatus.FAILED,
        dispatch_started_at=None,
        next_delivery_at=current + _retry_delay(envelope.delivery_attempts),
        last_delivery_error=_safe_error(error),
    )


def dispatch_pending_events_batch(
    *,
    limit: int = DEFAULT_BATCH_SIZE,
    enqueue: Enqueue | None = None,
    now=None,
) -> dict[str, int]:
    """Claim and hand off one batch, recording recoverable outcomes in SQL."""
    if enqueue is None:
        from apps.events.tasks import enqueue_event

        enqueue = enqueue_event

    events = claim_pending_events(limit=limit, now=now)
    published = 0
    failed = 0
    for envelope in events:
        try:
            enqueue(envelope)
        except Exception as error:
            mark_failed(envelope, error, now=now)
            failed += 1
            continue
        mark_published(envelope, now=now)
        published += 1
    return {"claimed": len(events), "published": published, "failed": failed}
