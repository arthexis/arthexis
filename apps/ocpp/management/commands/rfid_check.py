from django.core.management import call_command
from django.core.management.base import BaseCommand

from apps.cards.models import RFID


class Command(BaseCommand):
    """Deprecated wrapper for `rfid check`."""

    help = "[DEPRECATED] Use `manage.py rfid check` instead."

    def add_arguments(self, parser):
        target = parser.add_mutually_exclusive_group(required=True)
        target.add_argument("--label", help="Validate an RFID associated with the given label id or custom label.")
        target.add_argument("--uid", help="Validate an RFID by providing the UID value directly.")
        target.add_argument("--scan", action="store_true", help="Start the RFID scanner and return the first successfully read tag.")
        parser.add_argument("--kind", choices=[choice[0] for choice in RFID.KIND_CHOICES], help="Optional RFID kind when validating a UID directly.")
        parser.add_argument("--endianness", choices=[choice[0] for choice in RFID.ENDIANNESS_CHOICES], help="Optional endianness when validating a UID directly.")
        parser.add_argument("--timeout", type=float, default=5.0, help="How long to wait for a scan before timing out when running non-interactively (seconds).")
        parser.add_argument("--pretty", action="store_true", help="Pretty-print the JSON response.")

    def handle(self, *args, **options):
        self.stderr.write(self.style.WARNING("rfid_check is deprecated; use `manage.py rfid check` instead."))
        call_args = ["rfid", "check"]
        if options.get("label"):
            call_args.extend(["--label", options["label"]])
        if options.get("uid"):
            call_args.extend(["--uid", options["uid"]])
        if options.get("scan"):
            call_args.append("--scan")
        if options.get("kind"):
            call_args.extend(["--kind", options["kind"]])
        if options.get("endianness"):
            call_args.extend(["--endianness", options["endianness"]])
        if options.get("timeout") is not None:
            call_args.extend(["--timeout", str(options["timeout"])])
        if options.get("pretty"):
            call_args.append("--pretty")
        call_command(*call_args, stdout=self.stdout, stderr=self.stderr)
