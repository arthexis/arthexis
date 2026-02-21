"""Compatibility wrapper for ``python manage.py lcd calibrate``."""

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Delegate legacy lcd_calibrate invocations to the unified lcd command."""

    help = "Compatibility wrapper; use `python manage.py lcd calibrate` instead"

    def add_arguments(self, parser) -> None:
        parser.add_argument("--service", dest="service_name")
        parser.add_argument("--lock-file", dest="lock_file")
        parser.add_argument("--restart", action="store_true")

    def handle(self, *args, **options):
        call_command("lcd", "calibrate", **options)
