from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Deprecated wrapper for `rfid doctor`."""

    help = "[DEPRECATED] Use `manage.py rfid doctor` instead."

    def add_arguments(self, parser):
        parser.add_argument(
            "--timeout",
            type=float,
            help="Scan timeout in seconds when running non-interactively",
        )
        parser.add_argument("--scan", action="store_true", help="Attempt a scan via the RFID service after checks.")
        parser.add_argument("--deep-read", action="store_true", help="Toggle deep-read mode via the RFID service.")
        parser.add_argument("--no-input", action="store_true", help="Skip interactive prompts.")
        parser.add_argument("--show-raw", action="store_true", help="Show raw RFID values in output (default is masked).")

    def handle(self, *args, **options):
        self.stderr.write(self.style.WARNING("rfid_doctor is deprecated; use `manage.py rfid doctor` instead."))
        kwargs = {"stdout": self.stdout, "stderr": self.stderr}
        for key in ("timeout", "scan", "deep_read", "no_input", "show_raw"):
            if options.get(key) is not None:
                kwargs[key] = options[key]
        call_command("rfid", "doctor", **kwargs)
