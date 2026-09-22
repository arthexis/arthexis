from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from apps.events.models import EventEnvelope


class EventEnvelopeTests(TestCase):
    def test_event_defaults_are_durable_and_unpublished(self) -> None:
        envelope = EventEnvelope.objects.create(
            event_type="test.event",
            producer="tests",
            payload={"value": 1},
        )

        self.assertIsNotNone(envelope.event_id)
        self.assertIsNotNone(envelope.created_at)
        self.assertIsNone(envelope.published_at)
        self.assertEqual(envelope.payload, {"value": 1})

    def test_event_ids_are_unique(self) -> None:
        first = EventEnvelope.objects.create(
            event_type="test.first",
            producer="tests",
        )
        second = EventEnvelope.objects.create(
            event_type="test.second",
            producer="tests",
        )

        self.assertNotEqual(first.event_id, second.event_id)

    def test_events_are_ordered_newest_first(self) -> None:
        older = EventEnvelope.objects.create(
            event_type="test.older",
            producer="tests",
        )
        newer = EventEnvelope.objects.create(
            event_type="test.newer",
            producer="tests",
        )
        EventEnvelope.objects.filter(pk=older.pk).update(
            created_at=timezone.now() - timedelta(hours=1)
        )

        self.assertEqual(
            list(EventEnvelope.objects.values_list("event_type", flat=True)),
            ["test.newer", "test.older"],
        )
