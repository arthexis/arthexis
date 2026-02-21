"""Deprecated wrapper for ``python manage.py node check``."""

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Redirect legacy node check command."""

    help = (
        "DEPRECATED: use `python manage.py node check` instead of "
        "`python manage.py check_nodes`."
    )

    def handle(self, *args, **options):
        """Emit deprecation notice and invoke the new action."""

        self.stderr.write(
            self.style.WARNING(
                "`check_nodes` is deprecated; use `python manage.py node check`."
            )
        )
        call_command(
            "node",
            "check",
            stdout=options.get("stdout", self.stdout),
            stderr=options.get("stderr", self.stderr),
            skip_checks=options.get("skip_checks", False),
            force_color=options.get("force_color", False),
            no_color=options.get("no_color", False),
            verbosity=options.get("verbosity", 1),
            traceback=options.get("traceback", False),
        )
