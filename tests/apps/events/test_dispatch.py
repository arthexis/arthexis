from datetime import timedelta
from unittest.mock import Mock, patch

from django.test import TestCase
from django.utils import timezone

from apps.events.dispatch import (
    STALE_DISPATCH_AFTER,
    claim_pending_events,
    dispatch_pending_events_batch,
)
from apps.events.models import EventEnvelope
from apps.events.tasks import process_event


class EventOutboxDispatchTests(TestCase):
    def create_event(self, **overrides) -> EventEnvelope:
        values = {
            "event_type": "test.event",
            "producer": "tests",
            "payload": {"value": 1},
        }
        values.update(overrides)
        return EventEnvelope.objects.create(**values)

    def test_new_event_is_pending_for_delivery(self) -> None:
        envelope = self.create_event()

        self.assertEqual(
            envelope.delivery_status,
            EventEnvelope.DeliveryStatus.PENDING,
        )
        self.assertEqual(envelope.delivery_attempts, 0)
        self.assertIsNone(envelope.dispatch_started_at)
        self.assertIsNone(envelope.next_delivery_at)
        self.assertEqual(envelope.last_delivery_error, "")
        self.assertIsNone(envelope.published_at)

    def test_successful_handoff_marks_event_published(self) -> None:
        envelope = self.create_event()
        enqueue = Mock()

        result = dispatch_pending_events_batch(enqueue=enqueue)

        self.assertEqual(result, {"claimed": 1, "published": 1, "failed": 0})
        enqueue.assert_called_once()
        envelope.refresh_from_db()
        self.assertEqual(
            envelope.delivery_status,
            EventEnvelope.DeliveryStatus.PUBLISHED,
        )
        self.assertEqual(envelope.delivery_attempts, 1)
        self.assertIsNotNone(envelope.published_at)
        self.assertIsNone(envelope.dispatch_started_at)

    def test_broker_failure_is_recorded_for_retry(self) -> None:
        envelope = self.create_event()
        now = timezone.now()

        result = dispatch_pending_events_batch(
            enqueue=Mock(side_effect=ConnectionError("broker unavailable")),
            now=now,
        )

        self.assertEqual(result, {"claimed": 1, "published": 0, "failed": 1})
        envelope.refresh_from_db()
        self.assertEqual(
            envelope.delivery_status,
            EventEnvelope.DeliveryStatus.FAILED,
        )
        self.assertEqual(envelope.delivery_attempts, 1)
        self.assertEqual(
            envelope.last_delivery_error,
            "Broker handoff failed: ConnectionError",
        )
        self.assertEqual(envelope.next_delivery_at, now + timedelta(minutes=1))
        self.assertIsNone(envelope.published_at)

    def test_failed_event_is_not_retried_before_due_time(self) -> None:
        now = timezone.now()
        self.create_event(
            delivery_status=EventEnvelope.DeliveryStatus.FAILED,
            delivery_attempts=1,
            next_delivery_at=now + timedelta(minutes=1),
        )

        result = dispatch_pending_events_batch(enqueue=Mock(), now=now)

        self.assertEqual(result, {"claimed": 0, "published": 0, "failed": 0})

    def test_due_failed_event_is_retried(self) -> None:
        now = timezone.now()
        envelope = self.create_event(
            delivery_status=EventEnvelope.DeliveryStatus.FAILED,
            delivery_attempts=1,
            next_delivery_at=now - timedelta(seconds=1),
        )

        claimed = claim_pending_events(now=now)

        self.assertEqual([item.pk for item in claimed], [envelope.pk])
        envelope.refresh_from_db()
        self.assertEqual(envelope.delivery_attempts, 2)
        self.assertEqual(
            envelope.delivery_status,
            EventEnvelope.DeliveryStatus.DISPATCHING,
        )

    def test_stale_dispatching_event_is_reclaimed(self) -> None:
        now = timezone.now()
        envelope = self.create_event(
            delivery_status=EventEnvelope.DeliveryStatus.DISPATCHING,
            delivery_attempts=1,
            dispatch_started_at=now - STALE_DISPATCH_AFTER - timedelta(seconds=1),
        )

        claimed = claim_pending_events(now=now)

        self.assertEqual([item.pk for item in claimed], [envelope.pk])
        envelope.refresh_from_db()
        self.assertEqual(envelope.delivery_attempts, 2)
        self.assertEqual(envelope.dispatch_started_at, now)

    @patch("apps.events.tasks.process_event.delay")
    def test_enqueue_uses_only_durable_event_identity(self, delay) -> None:
        envelope = self.create_event()

        dispatch_pending_events_batch()

        delay.assert_called_once_with(str(envelope.event_id))


class EventProcessingTests(TestCase):
    def test_process_event_loads_envelope_and_invokes_subscriber(self) -> None:
        envelope = EventEnvelope.objects.create(
            event_type="test.process",
            producer="tests",
            payload={"value": 1},
        )
        handler = Mock()

        with patch(
            "apps.events.tasks.dispatch_to_subscribers",
            return_value=1,
        ) as dispatch:
            count = process_event(str(envelope.event_id))

        self.assertEqual(count, 1)
        dispatch.assert_called_once()
        self.assertEqual(dispatch.call_args.args[0].pk, envelope.pk)
