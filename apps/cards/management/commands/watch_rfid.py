"""Deprecated wrapper for ``rfid watch``."""

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Deprecated: use `python manage.py rfid watch` instead."

    def add_arguments(self, parser):
        parser.add_argument("--stop", action="store_true")

    def handle(self, *args, **options):
        self.stderr.write(self.style.WARNING("`watch_rfid` is deprecated. Use `python manage.py rfid watch`."))
        call_command("rfid", "watch", stop=options.get("stop", False))
