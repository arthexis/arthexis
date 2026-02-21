from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Deprecated wrapper for the unified health command."""

    help = "[DEPRECATED] Use `manage.py health --target core.next_upgrade`."

    def handle(self, *args, **options):  # noqa: D401 - inherited docstring
        self.stderr.write(
            self.style.WARNING(
                "check_next_upgrade is deprecated; use `manage.py health --target core.next_upgrade`."
            )
        )
        call_command(
            "health",
            target=["core.next_upgrade"],
            stdout=self.stdout,
            stderr=self.stderr,
        )
