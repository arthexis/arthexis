"""Compatibility wrapper for ``python manage.py lcd plan``."""

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Delegate legacy lcd_plan invocations to the unified lcd command."""

    help = "Compatibility wrapper; use `python manage.py lcd plan` instead"

    def add_arguments(self, parser) -> None:
        parser.add_argument("--seconds", type=int, default=60)
        parser.add_argument("-i", "--interactive", action="store_true")

    def handle(self, *args, **options):
        call_command("lcd", "plan", **options)
