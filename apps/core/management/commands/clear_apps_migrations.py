"""Backward-compatible shim for ``migrations clear``."""

from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Delegate to the unified ``migrations clear`` command."""

    help = "Deprecated alias for `migrations clear`; kept for compatibility."

    def add_arguments(self, parser):
        """Mirror compatible arguments for ``migrations clear``."""

        parser.add_argument(
            "--apps-dir",
            dest="apps_dir",
            help="Override the apps directory (defaults to settings.APPS_DIR)",
        )

    def handle(self, *args, **options):
        """Delegate to the new root command."""

        command_args = ["clear"]
        if options.get("apps_dir"):
            command_args.extend(["--apps-dir", options["apps_dir"]])
        call_command("migrations", *command_args, stdout=self.stdout, stderr=self.stderr)
