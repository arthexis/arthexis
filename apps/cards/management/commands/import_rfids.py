"""Deprecated wrapper for ``rfid import``."""

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Deprecated: use `python manage.py rfid import` instead."

    def add_arguments(self, parser):
        parser.add_argument("path")
        parser.add_argument("--color")
        parser.add_argument("--released")
        parser.add_argument("--account-field")

    def handle(self, *args, **options):
        self.stderr.write(self.style.WARNING("`import_rfids` is deprecated. Use `python manage.py rfid import`."))
        kwargs = {k.replace('-', '_'): v for k, v in options.items() if v is not None}
        path = kwargs.pop("path", None)
        if path is None:
            call_command("rfid", "import", **kwargs)
            return
        call_command("rfid", "import", path, **kwargs)
