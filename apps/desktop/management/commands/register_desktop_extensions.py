"""Deprecated desktop extension registration command."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    """Inform operators that extension registration is no longer supported."""

    help = "Deprecated: desktop extension registration is no longer supported."

    def handle(self, *args, **options):
        """Fail fast with replacement guidance for supported shortcut workflows."""
        raise CommandError(
            "register_desktop_extensions has been retired. "
            "Use explicit app-owned commands like sync_desktop_shortcuts and follow "
            "apps/docs/cookbooks/desktop-shortcuts-operations.md."
        )
