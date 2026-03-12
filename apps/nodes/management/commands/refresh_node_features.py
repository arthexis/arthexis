"""Compatibility wrapper for the legacy ``refresh_node_features`` command."""

from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Bridge legacy ``refresh_node_features`` calls to ``node refresh_features``."""

    help = "Legacy alias; use `python manage.py node refresh_features` instead."

    def handle(self, *args, **options):
        """Print legacy-alias notice and execute ``node refresh_features``."""

        self.stdout.write(
            self.style.WARNING(
                "LEGACY: `manage.py refresh_node_features` is a legacy alias; use `manage.py node refresh_features` instead."
            )
        )
        call_command(
            "node",
            "refresh_features",
            stdout=self.stdout,
            stderr=self.stderr,
        )
