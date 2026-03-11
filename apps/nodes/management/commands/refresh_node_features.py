"""Compatibility wrapper for the deprecated ``refresh_node_features`` command."""

from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Bridge legacy ``refresh_node_features`` calls to ``node refresh_features``."""

    help = "Deprecated; use `python manage.py node refresh_features` instead."

    def handle(self, *args, **options):
        """Print deprecation notice and execute ``node refresh_features``."""

        self.stdout.write(
            self.style.WARNING(
                "DEPRECATED: `manage.py refresh_node_features` is deprecated; use `manage.py node refresh_features` instead."
            )
        )
        call_command(
            "node",
            "refresh_features",
            stdout=self.stdout,
            stderr=self.stderr,
        )
