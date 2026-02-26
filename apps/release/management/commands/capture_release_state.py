from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand

from apps.core.management.deprecation import absorbed_into_command


@absorbed_into_command("release capture-state")
class Command(BaseCommand):
    """Deprecated wrapper for the unified release command."""

    help = "[DEPRECATED] Use `manage.py release capture-state <version>`."

    def add_arguments(self, parser):
        """Register compatibility arguments."""

        parser.add_argument("version", help="Release version to snapshot")

    def handle(self, *args, **options):
        """Delegate to ``release capture-state`` while preserving legacy syntax."""

        self.stderr.write(
            self.style.WARNING(
                "capture_release_state is deprecated; use `manage.py release capture-state <version>`."
            )
        )
        call_command(
            "release",
            "capture-state",
            options["version"],
            stdout=self.stdout,
            stderr=self.stderr,
        )
