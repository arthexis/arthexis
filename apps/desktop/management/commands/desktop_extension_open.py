"""Deprecated command retained to block removed extension execution flow."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    """Explain that extension-open dispatch was removed from runtime."""

    help = "Deprecated: desktop extension open dispatch is no longer supported."

    def add_arguments(self, parser):
        """Keep legacy flags so older scripts receive a clear failure."""
        parser.add_argument("--extension-id", required=False, type=int)
        parser.add_argument("--filename", default=None, required=False)

    def handle(self, *args, **options):
        """Fail fast and redirect operators to supported workflows."""
        raise CommandError(
            "desktop_extension_open has been retired with RegisteredExtension runtime "
            "execution. Use app-owned commands and documented runbooks in "
            "apps/docs/cookbooks/desktop-shortcuts-operations.md."
        )
