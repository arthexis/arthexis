"""Shortcut wrapper for the standalone ``refresh_node_features`` command."""

from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Forward standalone ``refresh_node_features`` calls to ``node refresh``."""

    help = "Shortcut for `python manage.py node refresh`."

    def handle(self, *args, **options):
        """Execute ``node refresh`` without standalone warning noise."""

        call_command(
            "node",
            "refresh",
            stdout=self.stdout,
            stderr=self.stderr,
        )
