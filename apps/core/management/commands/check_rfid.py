from django.core.management import call_command
from django.core.management.base import BaseCommand

from apps.cards.models import RFID


class Command(BaseCommand):
    """Deprecated wrapper for `rfid check`."""

    help = "[DEPRECATED] Use `manage.py rfid check --uid <value> [--kind ...]`."

    def add_arguments(self, parser):
        parser.add_argument("value", help="RFID value to validate")
        parser.add_argument("--kind", choices=[choice[0] for choice in RFID.KIND_CHOICES], help="Optional RFID kind to assign when registering a new tag")
        parser.add_argument("--pretty", action="store_true", help="Pretty-print the JSON response")

    def handle(self, *args, **options):
        self.stderr.write(self.style.WARNING("check_rfid is deprecated; use `manage.py rfid check --uid ...` instead."))
        call_args = ["rfid", "check", "--uid", options["value"]]
        if options.get("kind"):
            call_args.extend(["--kind", options["kind"]])
        if options.get("pretty"):
            call_args.append("--pretty")
        call_command(
            *call_args,
            stdout=self.stdout,
            stderr=self.stderr,
        )
