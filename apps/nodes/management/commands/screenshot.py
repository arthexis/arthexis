"""Compatibility wrapper for the legacy ``screenshot`` command."""

from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Bridge legacy ``screenshot`` calls to ``node screenshot``."""

    help = "Legacy alias; use `python manage.py node screenshot` instead."

    def add_arguments(self, parser) -> None:
        """Mirror legacy args and forward to the unified node command."""

        parser.add_argument("url", nargs="?")
        parser.add_argument("--freq", type=int)
        parser.add_argument("--local", action="store_true")

    def handle(self, *args, **options):
        """Print legacy-alias notice and execute ``node screenshot``."""

        self.stdout.write(
            self.style.WARNING(
                "LEGACY: `manage.py screenshot` is a legacy alias; use `manage.py node screenshot` instead."
            )
        )
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
