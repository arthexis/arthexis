"""Shortcut wrapper for the standalone ``message`` command."""

from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Forward standalone ``message`` calls to ``node message``."""

    help = "Shortcut for `python manage.py node message ...`."

    def add_arguments(self, parser) -> None:
        """Mirror standalone args and forward to the unified node command."""

        parser.add_argument("subject", help="Subject or first line of the message")
        parser.add_argument("body", nargs="?", default="", help="Optional body text")
        parser.add_argument("--reach", dest="reach")
        parser.add_argument("--seen", nargs="+", dest="seen")
        parser.add_argument("--lcd-channel-type", dest="lcd_channel_type")
        parser.add_argument("--lcd-channel-num", dest="lcd_channel_num", type=int)

    def handle(self, *args, **options):
        """Execute ``node message`` with the mirrored standalone arguments."""

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
