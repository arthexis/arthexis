from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Deprecated wrapper for the unified health command."""

    help = "[DEPRECATED] Use `manage.py health --target core.lcd_send`."

    def add_arguments(self, parser) -> None:
        parser.add_argument("subject", help="Text to send to the LCD display")
        parser.add_argument("--body", default="", help="Second line of the LCD message")
        parser.add_argument(
            "--expires-at",
            default=None,
            help="Optional expiration timestamp written to the lock file",
        )
        parser.add_argument(
            "--sticky",
            action="store_true",
            help="Write to the sticky (high-priority) lock file",
        )
        parser.add_argument(
            "--channel-type",
            default=None,
            help="LCD channel type to target (e.g. low, high, clock, uptime, custom)",
        )
        parser.add_argument(
            "--channel-num",
            default=None,
            help="LCD channel number to target when applicable",
        )
        parser.add_argument(
            "--timeout",
            type=float,
            default=10.0,
            help="Seconds to wait for the LCD daemon to process the message",
        )
        parser.add_argument(
            "--poll-interval",
            type=float,
            default=0.2,
            help="Seconds between lock-file checks",
        )

    def handle(self, *args, **options):
        self.stderr.write(
            self.style.WARNING(
                "check_lcd_send is deprecated; use `manage.py health --target core.lcd_send`."
            )
        )
        call_command(
            "health",
            target=["core.lcd_send"],
            lcd_subject=options["subject"],
            lcd_body=options["body"],
            lcd_expires_at=options.get("expires_at"),
            lcd_sticky=bool(options.get("sticky")),
            lcd_channel_type=options.get("channel_type"),
            lcd_channel_num=options.get("channel_num"),
            lcd_timeout=float(options.get("timeout", 10.0)),
            lcd_poll_interval=float(options.get("poll_interval", 0.2)),
            stdout=self.stdout,
            stderr=self.stderr,
        )
