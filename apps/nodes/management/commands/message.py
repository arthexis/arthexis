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

    def handle(self, *args, **options):
        """Execute ``node message`` with the mirrored standalone arguments."""

        call_command(
            "node",
            "message",
            options["subject"],
            options["body"],
            reach=options.get("reach"),
            seen=options.get("seen"),
            stdout=self.stdout,
            stderr=self.stderr,
        )
