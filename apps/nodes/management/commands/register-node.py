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

        self.stderr.write(
            self.style.WARNING(
                "`register-node` is deprecated; use `python manage.py node register`."
            )
        )
        call_command(
            "node",
            "register",
            options["token"],
            stdout=options.get("stdout", self.stdout),
            stderr=options.get("stderr", self.stderr),
            skip_checks=options.get("skip_checks", False),
            force_color=options.get("force_color", False),
            no_color=options.get("no_color", False),
            verbosity=options.get("verbosity", 1),
            traceback=options.get("traceback", False),
        )
