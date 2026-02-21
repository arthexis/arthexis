"""Deprecated wrapper for ``rfid service``."""

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Run the legacy ``rfid_service`` command name."""

    help = "Deprecated: use `python manage.py rfid service` instead."

    def add_arguments(self, parser):
        parser.add_argument("--host")
        parser.add_argument("--port", type=int)
        parser.add_argument("--debug", action="store_true")
        parser.add_argument("--no-debug", action="store_true")

    def handle(self, *args, **options):
        self.stderr.write(
            self.style.WARNING("`rfid_service` is deprecated. Use `python manage.py rfid service`.")
        )
        debug = options.get("debug")
        if options.get("no_debug"):
            debug = False
        kwargs = {"debug": debug if debug is not None else False}
        if options.get("host"):
            kwargs["host"] = options["host"]
        if options.get("port") is not None:
            kwargs["port"] = options["port"]
        call_command("rfid", "service", **kwargs)
