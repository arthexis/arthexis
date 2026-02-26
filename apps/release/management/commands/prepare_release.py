from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand

from apps.core.management.deprecation import absorbed_into_command


@absorbed_into_command("release prepare")
class Command(BaseCommand):
    """Deprecated wrapper for the unified release command."""

    help = "[DEPRECATED] Use `manage.py release prepare <version>`."

    def add_arguments(self, parser):
        """Register compatibility arguments."""

        parser.add_argument("version", help="Version string for the release")

    def handle(self, *args, **options):
        """Delegate to ``release prepare`` while preserving legacy syntax."""

        self.stderr.write(
            self.style.WARNING(
                "prepare_release is deprecated; use `manage.py release prepare <version>`."
            )
        )
        call_command(
            "release",
            "prepare",
            options["version"],
            stdout=self.stdout,
            stderr=self.stderr,
        )
