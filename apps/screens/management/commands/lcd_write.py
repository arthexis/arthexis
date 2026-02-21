"""Compatibility wrapper for ``python manage.py lcd write``."""

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Delegate legacy lcd_write invocations to the unified lcd command."""

    help = "Compatibility wrapper; use `python manage.py lcd write` instead"

    def add_arguments(self, parser):
        parser.add_argument("--subject")
        parser.add_argument("--body")
        parser.add_argument("--sticky", action="store_true")
        parser.add_argument("--delete", action="store_true")
        parser.add_argument("--restart", action="store_true")
        parser.add_argument(
            "--no-resolve",
            dest="resolve_sigils",
            action="store_false",
            default=True,
        )
        parser.add_argument("--service", dest="service_name")

    def handle(self, *args, **options):
        call_command("lcd", "write", **options)
