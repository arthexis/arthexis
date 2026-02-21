from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Deprecated wrapper for `rfid service`."""

    help = "[DEPRECATED] Use `manage.py rfid service` instead."

    def add_arguments(self, parser):
        parser.add_argument("--host")
        parser.add_argument("--port", type=int)
        parser.add_argument("--debug", action="store_true")
        parser.add_argument("--no-debug", action="store_true")

    def handle(self, *args, **options):
        self.stderr.write(self.style.WARNING("rfid_service is deprecated; use `manage.py rfid service` instead."))
        kwargs = {"stdout": self.stdout, "stderr": self.stderr}
        if options.get("host") is not None:
            kwargs["host"] = options["host"]
        if options.get("port") is not None:
            kwargs["port"] = options["port"]
        if options.get("debug"):
            kwargs["debug"] = True
        if options.get("no_debug"):
            kwargs["debug"] = False
        call_command("rfid", "service", **kwargs)
