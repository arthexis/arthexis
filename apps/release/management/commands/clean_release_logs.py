"""Deprecated wrapper command for removing release publish logs and lock files."""

from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand

from apps.core.management.deprecation import absorbed_into_command


@absorbed_into_command("release clean-logs")
class Command(BaseCommand):
    """Deprecated wrapper for the unified release command."""

    help = "[DEPRECATED] Use `manage.py release clean-logs ...`."

    def add_arguments(self, parser):
        """Register compatibility arguments."""

        parser.add_argument(
            "releases",
            nargs="*",
            metavar="PACKAGE:VERSION",
            help="Release identifier in the form <package>:<version>.",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            dest="clean_all",
            help="Remove all release publish logs and related lock files.",
        )

    def handle(self, *args, **options):
        """Delegate to ``release clean-logs`` while preserving legacy syntax."""

        self.stderr.write(
            self.style.WARNING(
                "clean_release_logs is deprecated; use `manage.py release clean-logs`."
            )
        )
        return call_command(
            "release",
            "clean-logs",
            *list(options.get("releases") or []),
            clean_all=options.get("clean_all", False),
            stdout=self.stdout,
            stderr=self.stderr,
        )
