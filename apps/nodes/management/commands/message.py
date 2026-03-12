"""Compatibility wrapper for the legacy ``message`` command."""

from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Bridge legacy ``message`` calls to ``node message``."""

    help = "Legacy alias; use `python manage.py node message ...` instead."

    def add_arguments(self, parser) -> None:
        """Mirror legacy args and forward to the unified node command."""

        parser.add_argument("subject", help="Subject or first line of the message")
        parser.add_argument("body", nargs="?", default="", help="Optional body text")
        parser.add_argument("--reach", dest="reach")
        parser.add_argument("--seen", nargs="+", dest="seen")
        parser.add_argument("--lcd-channel-type", dest="lcd_channel_type")
        parser.add_argument("--lcd-channel-num", dest="lcd_channel_num", type=int)

    def handle(self, *args, **options):
        """Print legacy-alias notice and execute ``node message``."""

        self.stdout.write(
            self.style.WARNING(
                "LEGACY: `manage.py message` is a legacy alias; use `manage.py node message` instead."
            )
        )
        call_command(
            "node",
            "message",
            options["subject"],
            options["body"],
            reach=options.get("reach"),
            seen=options.get("seen"),
            lcd_channel_type=options.get("lcd_channel_type"),
            lcd_channel_num=options.get("lcd_channel_num"),
            stdout=self.stdout,
            stderr=self.stderr,
        )
