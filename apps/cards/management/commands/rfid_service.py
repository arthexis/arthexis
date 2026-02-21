from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Deprecated wrapper for `rfid service`."""

    help = "[DEPRECATED] Use `manage.py rfid service` instead."

    def add_arguments(self, parser):
        parser.add_argument("--host", help="Host interface to bind the RFID service")
        parser.add_argument("--port", type=int, help="UDP port to bind the RFID service")
        debug_group = parser.add_mutually_exclusive_group()
        debug_group.add_argument("--debug", action="store_true", help="Enable debug logging")
        debug_group.add_argument("--no-debug", action="store_true", help="Disable debug logging")

    def handle(self, *args, **options):
        self.stderr.write(self.style.WARNING("rfid_service is deprecated; use `manage.py rfid service` instead."))
        kwargs = {"stdout": self.stdout, "stderr": self.stderr}
        if options.get("host") is not None:
            kwargs["host"] = options["host"]
        if options.get("port") is not None:
            kwargs["port"] = options["port"]
        if options.get("debug"):
            kwargs["debug"] = True
        elif options.get("no_debug"):
            kwargs["debug"] = False
        call_command("rfid", "service", **kwargs)
