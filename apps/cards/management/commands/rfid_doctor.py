"""Deprecated wrapper for ``rfid doctor``."""

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Deprecated: use `python manage.py rfid doctor` instead."

    def add_arguments(self, parser):
        parser.add_argument("--timeout", type=float)
        parser.add_argument("--scan", action="store_true")
        parser.add_argument("--deep-read", action="store_true")
        parser.add_argument("--no-input", action="store_true")
        parser.add_argument("--show-raw", action="store_true")

    def handle(self, *args, **options):
        self.stderr.write(self.style.WARNING("`rfid_doctor` is deprecated. Use `python manage.py rfid doctor`."))
        kwargs = {k: v for k, v in options.items() if v not in (None, False)}
        call_command("rfid", "doctor", **kwargs)
