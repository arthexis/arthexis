from unittest.mock import patch

from django.db import DatabaseError
from django.test import TestCase

from apps.events.models import EventEnvelope
from apps.events.services import publish, publish_safely


class PublishTests(TestCase):
    def test_publish_persists_valid_event(self) -> None:
        envelope = publish(
            event_type="test.event",
            producer="tests",
            payload={"value": 1},
        )

        self.assertEqual(envelope.event_type, "test.event")
        self.assertEqual(envelope.producer, "tests")
        self.assertEqual(envelope.payload, {"value": 1})
        self.assertIsNone(envelope.published_at)
        self.assertTrue(EventEnvelope.objects.filter(pk=envelope.pk).exists())

    def test_publish_copies_mapping_payload(self) -> None:
        payload = {"value": 1}

        envelope = publish(
            event_type="test.event",
            producer="tests",
            payload=payload,
        )
        payload["value"] = 2

        self.assertEqual(envelope.payload, {"value": 1})

    def test_publish_rejects_empty_event_type(self) -> None:
        with self.assertRaisesMessage(ValueError, "Event type is required."):
            publish(event_type="", producer="tests", payload={})

    def test_publish_rejects_empty_producer(self) -> None:
        with self.assertRaisesMessage(ValueError, "Event producer is required."):
            publish(event_type="test.event", producer="", payload={})

    def test_publish_rejects_non_json_serializable_payload(self) -> None:
        with self.assertRaises(TypeError):
            publish(
                event_type="test.event",
                producer="tests",
                payload={"bad": object()},
            )

        self.assertFalse(EventEnvelope.objects.exists())


class PublishSafelyTests(TestCase):
    def test_publish_safely_returns_persisted_event(self) -> None:
        envelope = publish_safely(
            event_type="test.event",
            producer="tests",
            payload={"value": 1},
        )

        self.assertIsNotNone(envelope)
        self.assertEqual(envelope.payload, {"value": 1})

    def test_publish_safely_contains_validation_failure(self) -> None:
        with self.assertLogs("apps.events.services", level="ERROR"):
            envelope = publish_safely(
                event_type="",
                producer="tests",
                payload={},
            )

        self.assertIsNone(envelope)

    def test_publish_safely_contains_serialization_failure(self) -> None:
        with self.assertLogs("apps.events.services", level="ERROR"):
            envelope = publish_safely(
                event_type="test.event",
                producer="tests",
                payload={"bad": object()},
            )

        self.assertIsNone(envelope)

    @patch("apps.events.services.publish", side_effect=DatabaseError("offline"))
    def test_publish_safely_contains_database_failure(self, _publish) -> None:
        with self.assertLogs("apps.events.services", level="ERROR"):
            envelope = publish_safely(
                event_type="test.event",
                producer="tests",
                payload={},
            )

        self.assertIsNone(envelope)
