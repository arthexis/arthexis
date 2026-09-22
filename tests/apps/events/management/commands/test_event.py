from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.events.models import EventEnvelope


class EventCommandTests(TestCase):
    def test_publish_creates_event_with_default_producer(self) -> None:
        output = StringIO()

        call_command(
            "event",
            "publish",
            "test.event",
            "--payload",
            '{"value": 1}',
            stdout=output,
        )

        envelope = EventEnvelope.objects.get()
        self.assertEqual(envelope.event_type, "test.event")
        self.assertEqual(envelope.producer, "command")
        self.assertEqual(envelope.payload, {"value": 1})
        self.assertEqual(output.getvalue().strip(), str(envelope.event_id))

    def test_pub_alias_accepts_explicit_producer(self) -> None:
        output = StringIO()

        call_command(
            "event",
            "pub",
            "test.event",
            "--producer",
            "tests",
            stdout=output,
        )

        envelope = EventEnvelope.objects.get()
        self.assertEqual(envelope.producer, "tests")

    def test_command_rejects_invalid_json(self) -> None:
        with self.assertRaisesMessage(CommandError, "Payload must be a JSON object."):
            call_command(
                "event",
                "publish",
                "test.event",
                "--payload",
                "{not-json}",
            )

        self.assertFalse(EventEnvelope.objects.exists())

    def test_command_rejects_non_object_json(self) -> None:
        for payload in ('["value"]', '"value"', "1", "null"):
            with self.subTest(payload=payload):
                with self.assertRaisesMessage(
                    CommandError,
                    "Payload must be a JSON object.",
                ):
                    call_command(
                        "event",
                        "publish",
                        "test.event",
                        "--payload",
                        payload,
                    )

        self.assertFalse(EventEnvelope.objects.exists())
