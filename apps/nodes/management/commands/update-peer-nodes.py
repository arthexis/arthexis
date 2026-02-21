"""Deprecated wrapper for ``python manage.py node peers``."""

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Redirect legacy peer refresh command."""

    help = (
        "DEPRECATED: use `python manage.py node peers` instead of "
        "`python manage.py update-peer-nodes`."
    )

    def handle(self, *args, **options):
        """Emit deprecation notice and invoke the new action."""

        self.stdout.write(
            self.style.WARNING(
                "`update-peer-nodes` is deprecated; use `python manage.py node peers`."
            )
        )
        call_command("node", "peers")
