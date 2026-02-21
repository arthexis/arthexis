"""Deprecated wrapper for ``python manage.py node discover``."""

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Redirect legacy LAN discovery command."""

    help = (
        "DEPRECATED: use `python manage.py node discover` instead of "
        "`python manage.py lan-find-node`."
    )

    def add_arguments(self, parser):
        """Accept legacy discovery options."""

        parser.add_argument("--ports", default="8888,80,443")
        parser.add_argument("--timeout", type=float, default=2.0)
        parser.add_argument("--max-hosts", type=int, default=256)
        parser.add_argument("--interfaces", default="eth0,wlan0")

    def handle(self, *args, **options):
        """Emit deprecation notice and invoke the new action."""

        self.stderr.write(
            self.style.WARNING(
                "`lan-find-node` is deprecated; use `python manage.py node discover`."
            )
        )
        call_command(
            "node",
            "discover",
            ports=options["ports"],
            timeout=options["timeout"],
            max_hosts=options["max_hosts"],
            interfaces=options["interfaces"],
            stdout=options.get("stdout", self.stdout),
            stderr=options.get("stderr", self.stderr),
            skip_checks=options.get("skip_checks", False),
            force_color=options.get("force_color", False),
            no_color=options.get("no_color", False),
            verbosity=options.get("verbosity", 1),
            traceback=options.get("traceback", False),
        )
