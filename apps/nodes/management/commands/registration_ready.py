"""Deprecated wrapper for ``python manage.py node ready``."""

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Redirect legacy readiness checks to the unified node command."""

    help = (
        "DEPRECATED: use `python manage.py node ready` instead of "
        "`python manage.py registration_ready`."
    )

    def handle(self, *args, **options):
        """Emit deprecation notice and invoke the new action."""

        self.stdout.write(
            self.style.WARNING(
                "`registration_ready` is deprecated; use `python manage.py node ready`."
            )
        )
        call_command("node", "ready")
