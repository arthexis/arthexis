from unittest.mock import Mock

from django.test import TestCase

from apps.events.models import EventEnvelope
from apps.events.registry import _handlers, dispatch_to_subscribers, subscribe


class EventRegistryTests(TestCase):
    def tearDown(self) -> None:
        _handlers.clear()

    def test_registered_subscribers_receive_matching_event(self) -> None:
        handler = Mock()
        subscribe("test.event", handler)
        envelope = EventEnvelope.objects.create(
            event_type="test.event",
            producer="tests",
        )

        count = dispatch_to_subscribers(envelope)

        self.assertEqual(count, 1)
        handler.assert_called_once_with(envelope)

    def test_subscription_is_idempotent(self) -> None:
        handler = Mock()
        subscribe("test.event", handler)
        subscribe("test.event", handler)
        envelope = EventEnvelope.objects.create(
            event_type="test.event",
            producer="tests",
        )

        self.assertEqual(dispatch_to_subscribers(envelope), 1)
