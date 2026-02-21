"""Deprecated wrapper for ``python manage.py node register_curl``."""

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Redirect legacy curl script generator command."""

    help = (
        "DEPRECATED: use `python manage.py node register_curl <upstream>` instead of "
        "`python manage.py register-node-curl <upstream>`."
    )

    def add_arguments(self, parser):
        """Accept legacy arguments and map to the new action."""

        parser.add_argument("upstream")
        parser.add_argument("--local-base", default="https://localhost:8888")
        parser.add_argument("--token", default="")

    def handle(self, *args, **options):
        """Emit deprecation notice and invoke the new action."""

        self.stderr.write(
            self.style.WARNING(
                "`register-node-curl` is deprecated; use `python manage.py node register_curl`."
            )
        )
        call_command(
            "node",
            "register_curl",
            options["upstream"],
            local_base=options["local_base"],
            token=options["token"],
            stdout=options.get("stdout", self.stdout),
            stderr=options.get("stderr", self.stderr),
            skip_checks=options.get("skip_checks", False),
            force_color=options.get("force_color", False),
            no_color=options.get("no_color", False),
            verbosity=options.get("verbosity", 1),
            traceback=options.get("traceback", False),
        )
