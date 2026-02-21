"""Compatibility wrapper for ``python manage.py lcd animate``."""

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Delegate legacy lcd_animate invocations to the unified lcd command."""

    help = "Compatibility wrapper; use `python manage.py lcd animate` instead"

    def add_arguments(self, parser):
        parser.add_argument("slug", nargs="?")
        parser.add_argument("--loops", type=int, default=1)
        parser.add_argument("--interval", type=int)

    def handle(self, *args, **options):
        call_command("lcd", "animate", **options)
