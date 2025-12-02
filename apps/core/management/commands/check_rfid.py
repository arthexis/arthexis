import json

from django.core.management.base import BaseCommand, CommandError

from apps.cards.models import RFID
from apps.cards.reader import validate_rfid_value


class Command(BaseCommand):
    help = "Validate a manually entered RFID value using the scanner logic."

    def add_arguments(self, parser):
        parser.add_argument("value", help="RFID value to validate")
        parser.add_argument(
            "--kind",
            choices=[choice[0] for choice in RFID.KIND_CHOICES],
            help="Optional RFID kind to assign when registering a new tag",
        )
        parser.add_argument(
            "--pretty",
            action="store_true",
            help="Pretty-print the JSON response",
        )

    def handle(self, *args, **options):
        value = options["value"]
        kind = options.get("kind")
        pretty = options["pretty"]

        result = validate_rfid_value(value, kind=kind)
        if "error" in result:
            raise CommandError(result["error"])

        dump_kwargs = {"indent": 2, "sort_keys": True} if pretty else {}
        payload = json.dumps(result, **dump_kwargs)
        self.stdout.write(payload)
