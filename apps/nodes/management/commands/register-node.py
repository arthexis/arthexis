"""Deprecated wrapper for ``python manage.py node register``."""

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Redirect legacy command usage to the unified node command."""

    help = (
        "DEPRECATED: use `python manage.py node register <token>` instead of "
        "`python manage.py register-node <token>`."
    )

    def add_arguments(self, parser):
        """Accept the legacy token argument."""

        parser.add_argument("token")

    def handle(self, *args, **options):
        """Emit deprecation notice and invoke the new action."""

        self.stdout.write(
            self.style.WARNING(
                "`register-node` is deprecated; use `python manage.py node register`."
            )
        )
        call_command("node", "register", options["token"])
