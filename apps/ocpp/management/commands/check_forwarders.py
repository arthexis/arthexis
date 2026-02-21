from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Deprecated wrapper for the unified health command."""

    help = "[DEPRECATED] Use `manage.py health --target ocpp.forwarders`."

    def handle(self, *args, **options) -> None:
        self.stderr.write(
            self.style.WARNING(
                "check_forwarders is deprecated; use `manage.py health --target ocpp.forwarders`."
            )
        )
        call_command(
            "health",
            target=["ocpp.forwarders"],
            stdout=self.stdout,
            stderr=self.stderr,
        )
