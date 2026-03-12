"""Backward-compatible shim for ``migrations rebuild``."""

from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Delegate to the unified ``migrations rebuild`` command."""

    help = "Legacy alias for `migrations rebuild`; kept for compatibility."

    def add_arguments(self, parser):
        """Mirror compatible arguments for ``migrations rebuild``."""

        parser.add_argument(
            "--apps-dir",
            dest="apps_dir",
            help="Override the apps directory (defaults to settings.APPS_DIR)",
        )
        parser.add_argument(
            "--branch-id",
            dest="branch_id",
            help="Stable identifier recorded by the branch tag operation.",
        )

    def handle(self, *args, **options):
        """Delegate to the new root command."""

        command_args = ["rebuild"]
        if options.get("apps_dir"):
            command_args.extend(["--apps-dir", options["apps_dir"]])
        if options.get("branch_id"):
            command_args.extend(["--branch-id", options["branch_id"]])
        call_command("migrations", *command_args, stdout=self.stdout, stderr=self.stderr)
