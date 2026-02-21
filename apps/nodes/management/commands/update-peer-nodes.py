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

        self.stderr.write(
            self.style.WARNING(
                "`update-peer-nodes` is deprecated; use `python manage.py node peers`."
            )
        )
        call_command(
            "node",
            "peers",
            stdout=options.get("stdout", self.stdout),
            stderr=options.get("stderr", self.stderr),
            skip_checks=options.get("skip_checks", False),
            force_color=options.get("force_color", False),
            no_color=options.get("no_color", False),
            verbosity=options.get("verbosity", 1),
            traceback=options.get("traceback", False),
        )
