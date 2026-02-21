from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Deprecated wrapper for `rfid doctor`."""

    help = "[DEPRECATED] Use `manage.py rfid doctor` instead."

    def add_arguments(self, parser):
        parser.add_argument("--timeout", type=float)
        parser.add_argument("--scan", action="store_true")
        parser.add_argument("--deep-read", action="store_true")
        parser.add_argument("--no-input", action="store_true")
        parser.add_argument("--show-raw", action="store_true")

    def handle(self, *args, **options):
        self.stderr.write(self.style.WARNING("rfid_doctor is deprecated; use `manage.py rfid doctor` instead."))
        kwargs = {"stdout": self.stdout, "stderr": self.stderr}
        for key in ("timeout", "scan", "deep_read", "no_input", "show_raw"):
            if options.get(key) is not None:
                kwargs[key] = options[key]
        call_command("rfid", "doctor", **kwargs)
