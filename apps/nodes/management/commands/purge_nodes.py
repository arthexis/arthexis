"""Compatibility wrapper for the legacy ``purge_nodes`` command."""

from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Bridge legacy ``purge_nodes`` calls to ``node purge_nodes``."""

    help = "Legacy alias; use `python manage.py node purge_nodes` instead."

    def add_arguments(self, parser):
        """Mirror legacy args and forward to the unified node command."""

        parser.add_argument("--remove-anonymous", action="store_true", dest="remove_anonymous")

    def handle(self, *args, **options):
        """Print legacy-alias notice and execute ``node purge_nodes``."""

        self.stdout.write(
            self.style.WARNING(
                "LEGACY: `manage.py purge_nodes` is a legacy alias; use `manage.py node purge_nodes` instead."
            )
        )
        call_command(
            "node",
            "purge_nodes",
            remove_anonymous=options.get("remove_anonymous", False),
            stdout=self.stdout,
            stderr=self.stderr,
        )
