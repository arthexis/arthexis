from django.core.management import call_command
from django.core.management.base import BaseCommand

from apps.cards.models import RFID


class Command(BaseCommand):
    """Deprecated wrapper for `rfid check`."""

    help = "[DEPRECATED] Use `manage.py rfid check` instead."

    def add_arguments(self, parser):
        target = parser.add_mutually_exclusive_group(required=True)
        target.add_argument("--label")
        target.add_argument("--uid")
        target.add_argument("--scan", action="store_true")
        parser.add_argument("--kind", choices=[choice[0] for choice in RFID.KIND_CHOICES])
        parser.add_argument("--endianness", choices=[choice[0] for choice in RFID.ENDIANNESS_CHOICES])
        parser.add_argument("--timeout", type=float, default=5.0)
        parser.add_argument("--pretty", action="store_true")

    def handle(self, *args, **options):
        self.stderr.write(self.style.WARNING("rfid_check is deprecated; use `manage.py rfid check` instead."))
        kwargs = {"stdout": self.stdout, "stderr": self.stderr}
        for key in ("label", "uid", "scan", "kind", "endianness", "timeout", "pretty"):
            value = options.get(key)
            if value is not None:
                kwargs[key] = value
        call_command("rfid", "check", **kwargs)
