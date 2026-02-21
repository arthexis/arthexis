"""Compatibility wrapper for ``python manage.py lcd debug``."""

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Delegate legacy lcd_debug invocations to the unified lcd command."""

    help = "Compatibility wrapper; use `python manage.py lcd debug` instead"

    def add_arguments(self, parser):
        parser.add_argument("--long", action="store_true", dest="long_wait")
        parser.add_argument("--double", action="store_true")
        parser.add_argument("--outfile", type=str)

    def handle(self, *args, **options):
        call_command("lcd", "debug", **options)
