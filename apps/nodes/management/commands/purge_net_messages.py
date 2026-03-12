"""Compatibility wrapper for the legacy ``purge_net_messages`` command."""

from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Bridge legacy ``purge_net_messages`` calls to ``node purge_net_messages``."""

    help = "Legacy alias; use `python manage.py node purge_net_messages` instead."

    def handle(self, *args, **options):
        """Print legacy-alias notice and execute ``node purge_net_messages``."""

        self.stdout.write(
            self.style.WARNING(
                "LEGACY: `manage.py purge_net_messages` is a legacy alias; use `manage.py node purge_net_messages` instead."
            )
        )
        call_command(
            "node",
            "purge_net_messages",
            stdout=self.stdout,
            stderr=self.stderr,
        )
