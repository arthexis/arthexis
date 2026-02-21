"""Compatibility wrapper for ``python manage.py lcd replay``."""

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Delegate legacy lcd_replay invocations to the unified lcd command."""

    help = "Compatibility wrapper; use `python manage.py lcd replay` instead"

    def add_arguments(self, parser) -> None:
        parser.add_argument("--at", dest="timestamp")
        parser.add_argument("--days", type=int, default=0)
        parser.add_argument("--hours", type=int, default=0)
        parser.add_argument("--minutes", type=int, default=0)
        parser.add_argument("--service", dest="service_name")

    def handle(self, *args, **options):
        call_command("lcd", "replay", **options)
