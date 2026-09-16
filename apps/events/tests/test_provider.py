from __future__ import annotations

import json
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from apps.events.provider import publish_event


class EventProviderTests(SimpleTestCase):
    def test_publish_event_defaults_queue_to_event_type(self) -> None:
        with patch("apps.events.provider.publish_queue_event", return_value=True) as publish:
            self.assertTrue(publish_event("rfid.scanned", data={"uid": "04A1"}))

        publish.assert_called_once_with("rfid.scanned", "rfid.scanned", uid="04A1")

    def test_publish_event_allows_queue_override(self) -> None:
        with patch("apps.events.provider.publish_queue_event", return_value=True) as publish:
            self.assertTrue(
                publish_event(
                    "service.failed",
                    queue="service.events",
                    data={"service": "arthexis"},
                )
            )

        publish.assert_called_once_with(
            "service.events",
            "service.failed",
            service="arthexis",
        )

    def test_publish_event_rejects_empty_queue_override(self) -> None:
        for queue in ("", "   "):
            with self.subTest(queue=queue):
                with self.assertRaisesMessage(ValueError, "event queue cannot be empty"):
                    publish_event("service.failed", queue=queue)

    def test_event_command_supports_publish_and_structured_data(self) -> None:
        stdout = StringIO()
        with patch(
            "apps.events.management.commands.event.publish_event", return_value=True
        ) as publish:
            result = call_command(
                "event",
                "publish",
                "rfid.scanned",
                "--field",
                "uid=04A1",
                "--field",
                "reader=mfrc522",
                "--field",
                "count=2",
                stdout=stdout,
            )

        self.assertTrue(result["published"])
        publish.assert_called_once_with(
            "rfid.scanned",
            queue=None,
            data={"uid": "04A1", "reader": "mfrc522", "count": 2},
        )
        self.assertEqual(json.loads(stdout.getvalue())["queue"], "rfid.scanned")

    def test_event_command_supports_pub_queue_override_and_json_data(self) -> None:
        stdout = StringIO()
        with patch(
            "apps.events.management.commands.event.publish_event", return_value=True
        ) as publish:
            call_command(
                "event",
                "pub",
                "service.failed",
                "--queue",
                "service.events",
                "--data",
                '{"service":"arthexis","retry":false}',
                stdout=stdout,
            )

        publish.assert_called_once_with(
            "service.failed",
            queue="service.events",
            data={"service": "arthexis", "retry": False},
        )

    def test_event_command_rejects_empty_json_data(self) -> None:
        with self.assertRaises(CommandError):
            call_command("event", "pub", "service.failed", "--data", "")
