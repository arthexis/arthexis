from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Deprecated wrapper for the unified health command."""

    help = "[DEPRECATED] Use `manage.py health --target core.lcd_service`."

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirmed",
            action="store_true",
            help="Confirm the displayed random LCD text without interactive input.",
        )

    def handle(self, *args, **options):
        self.stderr.write(
            self.style.WARNING(
                "check_lcd_service is deprecated; use `manage.py health --target core.lcd_service`."
            )
        )
        call_command(
            "health",
            target=["core.lcd_service"],
            lcd_confirmed=bool(options.get("confirmed")),
            stdout=self.stdout,
            stderr=self.stderr,
        )
