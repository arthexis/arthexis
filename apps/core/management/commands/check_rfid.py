from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand

from apps.cards.models import RFID


class Command(BaseCommand):
    """Deprecated wrapper for the unified health command."""

    help = "[DEPRECATED] Use `manage.py health --target core.rfid`."

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
        self.stderr.write(
            self.style.WARNING(
                "check_rfid is deprecated; use `manage.py health --target core.rfid --rfid-value ...`."
            )
        )
        call_command(
            "health",
            target=["core.rfid"],
            rfid_value=options["value"],
            rfid_kind=options.get("kind"),
            rfid_pretty=bool(options.get("pretty")),
            stdout=self.stdout,
            stderr=self.stderr,
        )
