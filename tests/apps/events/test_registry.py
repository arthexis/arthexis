from unittest.mock import Mock

from django.test import TestCase

from apps.events.models import EventEnvelope
from apps.events.registry import dispatch_to_subscribers, subscribe


class EventRegistryTests(TestCase):
    def test_registered_subscribers_receive_matching_event(self) -> None:
        handler = Mock()
        subscribe("test.matching", handler)
        envelope = EventEnvelope.objects.create(
            event_type="test.matching",
            producer="tests",
        )

        count = dispatch_to_subscribers(envelope)

        self.assertEqual(count, 1)
        handler.assert_called_once_with(envelope)

    def test_subscription_is_idempotent(self) -> None:
        handler = Mock()
        subscribe("test.idempotent", handler)
        subscribe("test.idempotent", handler)
        envelope = EventEnvelope.objects.create(
            event_type="test.idempotent",
            producer="tests",
        )

        self.assertEqual(dispatch_to_subscribers(envelope), 1)
