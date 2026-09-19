"""Publish structured events from the Django command line."""

import json

from django.core.management.base import BaseCommand, CommandError

from apps.events.services import publish


class Command(BaseCommand):
    help = "Publishes a structured event. The 'pub' action is an alias for 'publish'."

    def add_arguments(self, parser):
        parser.add_argument("action", choices=("publish", "pub"))
        parser.add_argument("event_type")
        parser.add_argument("--producer", default="command")
        parser.add_argument("--payload", default="{}")

    def handle(self, *args, **options):
        try:
            payload = json.loads(options["payload"])
        except json.JSONDecodeError as error:
            raise CommandError("Payload must be a JSON object.") from error
        if not isinstance(payload, dict):
            raise CommandError("Payload must be a JSON object.")

        envelope = publish(
            event_type=options["event_type"],
            producer=options["producer"],
            payload=payload,
        )
        self.stdout.write(self.style.SUCCESS(str(envelope.event_id)))
