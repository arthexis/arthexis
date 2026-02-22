from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand
from apps.core.management.deprecation import absorbed_into_command


@absorbed_into_command("health --target core.time")
class Command(BaseCommand):
    """Deprecated wrapper for the unified health command."""

    help = "[DEPRECATED] Use `manage.py health --target core.time`."

    def handle(self, *args, **options):
        self.stderr.write(
            self.style.WARNING(
                "check_time is deprecated; use `manage.py health --target core.time`."
            )
        )
        call_command("health", target=["core.time"], stdout=self.stdout, stderr=self.stderr)
