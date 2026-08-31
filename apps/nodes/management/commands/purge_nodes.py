"""Shortcut wrapper for the standalone ``purge_nodes`` command."""

from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Forward standalone ``purge_nodes`` calls to ``node purge``."""

    help = "Shortcut for `python manage.py node purge`."

    def add_arguments(self, parser):
        """Mirror standalone args and forward to the unified node command."""

        parser.add_argument(
            "--remove-anonymous", action="store_true", dest="remove_anonymous"
        )

    def handle(self, *args, **options):
        """Execute ``node purge`` with the mirrored standalone arguments."""

        call_command(
            "node",
            "purge",
            remove_anonymous=options.get("remove_anonymous", False),
            stdout=self.stdout,
            stderr=self.stderr,
        )
