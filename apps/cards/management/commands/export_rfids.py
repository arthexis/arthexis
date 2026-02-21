"""Deprecated wrapper for ``rfid export``."""

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Deprecated: use `python manage.py rfid export` instead."

    def add_arguments(self, parser):
        parser.add_argument("path", nargs="?")
        parser.add_argument("--color")
        parser.add_argument("--released")
        parser.add_argument("--account-field")

    def handle(self, *args, **options):
        self.stderr.write(self.style.WARNING("`export_rfids` is deprecated. Use `python manage.py rfid export`."))
        kwargs = {k.replace('-', '_'): v for k, v in options.items() if v is not None}
        call_command("rfid", "export", **kwargs)
