from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from apps.events.models import EventEnvelope


class EventCommandTests(TestCase):
    def test_publish_alias_creates_event(self) -> None:
        output = StringIO()

        call_command(
            "event",
            "pub",
            "test.event",
            "--producer",
            "tests",
            "--payload",
            '{"value": 1}',
            stdout=output,
        )

        envelope = EventEnvelope.objects.get()
        self.assertEqual(envelope.payload, {"value": 1})
        self.assertEqual(envelope.producer, "tests")
