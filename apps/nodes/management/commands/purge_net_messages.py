"""Shortcut wrapper for the standalone ``purge_net_messages`` command."""

from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Forward standalone ``purge_net_messages`` calls to ``node purge-messages``."""

    help = "Shortcut for `python manage.py node purge-messages`."

    def handle(self, *args, **options):
        """Execute ``node purge-messages`` without standalone warning noise."""

        call_command(
            "node",
            "purge-messages",
            stdout=self.stdout,
            stderr=self.stderr,
        )
