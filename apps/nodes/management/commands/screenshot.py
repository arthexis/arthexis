"""Shortcut wrapper for the standalone ``screenshot`` command."""

from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Forward standalone ``screenshot`` calls to ``node screenshot``."""

    help = "Shortcut for `python manage.py node screenshot`."

    def add_arguments(self, parser) -> None:
        """Mirror standalone args and forward to the unified node command."""

        parser.add_argument("url", nargs="?")
        parser.add_argument("--freq", type=int)
        parser.add_argument("--local", action="store_true")

    def handle(self, *args, **options):
        """Execute ``node screenshot`` with the mirrored standalone arguments."""

        args = ["screenshot"]
        if options.get("url"):
            args.append(options["url"])
        call_command(
            "node",
            *args,
            freq=options.get("freq"),
            local=options.get("local", False),
            stdout=self.stdout,
            stderr=self.stderr,
        )
