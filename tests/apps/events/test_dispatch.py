from datetime import timedelta
from unittest.mock import Mock, patch

import pytest
from django.utils import timezone

from apps.events.dispatch import (
    STALE_DISPATCH_AFTER,
    claim_pending_events,
    dispatch_pending_events_batch,
)
from apps.events.models import EventEnvelope
from apps.events.tasks import process_event

pytestmark = pytest.mark.django_db


def test_new_event_is_pending_for_delivery(event_factory) -> None:
    envelope = event_factory()

    assert envelope.delivery_status == EventEnvelope.DeliveryStatus.PENDING
    assert envelope.delivery_attempts == 0
    assert envelope.dispatch_started_at is None
    assert envelope.next_delivery_at is None
    assert envelope.last_delivery_error == ""
    assert envelope.published_at is None


def test_successful_handoff_marks_event_published(event_factory) -> None:
    envelope = event_factory()
    enqueue = Mock()

    result = dispatch_pending_events_batch(enqueue=enqueue)

    assert result == {"claimed": 1, "published": 1, "failed": 0}
    enqueue.assert_called_once()
    envelope.refresh_from_db()
    assert envelope.delivery_status == EventEnvelope.DeliveryStatus.PUBLISHED
    assert envelope.delivery_attempts == 1
    assert envelope.published_at is not None
    assert envelope.dispatch_started_at is None


def test_broker_failure_is_recorded_for_retry(event_factory) -> None:
    envelope = event_factory()
    now = timezone.now()

    result = dispatch_pending_events_batch(
        enqueue=Mock(side_effect=ConnectionError("broker unavailable")),
        now=now,
    )

    assert result == {"claimed": 1, "published": 0, "failed": 1}
    envelope.refresh_from_db()
    assert envelope.delivery_status == EventEnvelope.DeliveryStatus.FAILED
    assert envelope.delivery_attempts == 1
    assert envelope.last_delivery_error == "Broker handoff failed: ConnectionError"
    assert envelope.next_delivery_at == now + timedelta(minutes=1)
    assert envelope.published_at is None


def test_failed_event_is_not_retried_before_due_time(event_factory) -> None:
    now = timezone.now()
    event_factory(
        delivery_status=EventEnvelope.DeliveryStatus.FAILED,
        delivery_attempts=1,
        next_delivery_at=now + timedelta(minutes=1),
    )

    result = dispatch_pending_events_batch(enqueue=Mock(), now=now)

    assert result == {"claimed": 0, "published": 0, "failed": 0}


def test_due_failed_event_is_retried(event_factory) -> None:
    now = timezone.now()
    envelope = event_factory(
        delivery_status=EventEnvelope.DeliveryStatus.FAILED,
        delivery_attempts=1,
        next_delivery_at=now - timedelta(seconds=1),
    )

    claimed = claim_pending_events(now=now)

    assert [item.pk for item in claimed] == [envelope.pk]
    envelope.refresh_from_db()
    assert envelope.delivery_attempts == 2
    assert envelope.delivery_status == EventEnvelope.DeliveryStatus.DISPATCHING


def test_stale_dispatching_event_is_reclaimed(event_factory) -> None:
    now = timezone.now()
    envelope = event_factory(
        delivery_status=EventEnvelope.DeliveryStatus.DISPATCHING,
        delivery_attempts=1,
        dispatch_started_at=now - STALE_DISPATCH_AFTER - timedelta(seconds=1),
    )

    claimed = claim_pending_events(now=now)

    assert [item.pk for item in claimed] == [envelope.pk]
    envelope.refresh_from_db()
    assert envelope.delivery_attempts == 2
    assert envelope.dispatch_started_at == now


def test_broker_acceptance_then_dispatcher_crash_is_reclaimed_and_reenqueued(
    event_factory,
) -> None:
    envelope = event_factory()
    first_handoff = Mock()
    claimed_at = timezone.now()

    with patch(
        "apps.events.dispatch.mark_published",
        side_effect=RuntimeError("dispatcher crashed after broker acceptance"),
    ):
        with pytest.raises(
            RuntimeError,
            match="dispatcher crashed after broker acceptance",
        ):
            dispatch_pending_events_batch(
                enqueue=first_handoff,
                now=claimed_at,
            )

    first_handoff.assert_called_once()
    envelope.refresh_from_db()
    assert envelope.delivery_status == EventEnvelope.DeliveryStatus.DISPATCHING
    assert envelope.delivery_attempts == 1
    assert envelope.published_at is None

    retry_handoff = Mock()
    recovered_at = claimed_at + STALE_DISPATCH_AFTER + timedelta(seconds=1)

    result = dispatch_pending_events_batch(
        enqueue=retry_handoff,
        now=recovered_at,
    )

    assert result == {"claimed": 1, "published": 1, "failed": 0}
    retry_handoff.assert_called_once()
    retried = retry_handoff.call_args.args[0]
    assert retried.event_id == envelope.event_id
    envelope.refresh_from_db()
    assert envelope.delivery_status == EventEnvelope.DeliveryStatus.PUBLISHED
    assert envelope.delivery_attempts == 2
    assert envelope.published_at is not None


def test_enqueue_uses_only_durable_event_identity(event_factory) -> None:
    envelope = event_factory()

    with patch("apps.events.tasks.process_event.delay") as delay:
        dispatch_pending_events_batch()

    delay.assert_called_once_with(str(envelope.event_id))


def test_worker_loss_policy_redelivers_unacknowledged_processing() -> None:
    assert process_event.acks_late
    assert process_event.reject_on_worker_lost


def test_subscriber_failure_propagates_before_late_ack(event_factory) -> None:
    envelope = event_factory(event_type="test.retry")

    with patch(
        "apps.events.tasks.dispatch_to_subscribers",
        side_effect=RuntimeError("worker lost before completion"),
    ):
        with pytest.raises(RuntimeError, match="worker lost before completion"):
            process_event(str(envelope.event_id))

    with patch(
        "apps.events.tasks.dispatch_to_subscribers",
        return_value=1,
    ) as dispatch:
        count = process_event(str(envelope.event_id))

    assert count == 1
    dispatch.assert_called_once()
    assert dispatch.call_args.args[0].event_id == envelope.event_id


def test_process_event_loads_envelope_and_invokes_subscriber(event_factory) -> None:
    envelope = event_factory(event_type="test.process")

    with patch(
        "apps.events.tasks.dispatch_to_subscribers",
        return_value=1,
    ) as dispatch:
        count = process_event(str(envelope.event_id))

    assert count == 1
    dispatch.assert_called_once()
    assert dispatch.call_args.args[0].pk == envelope.pk
