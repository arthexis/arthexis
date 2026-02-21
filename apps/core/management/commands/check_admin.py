from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Deprecated wrapper for the unified health command."""

    help = "[DEPRECATED] Use `manage.py health --target core.admin`."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Create or repair the default admin account when issues are detected.",
        )

    def handle(self, *args, **options):
        self.stderr.write(
            self.style.WARNING(
                "check_admin is deprecated; use `manage.py health --target core.admin`."
            )
        )
        call_command(
            "health",
            target=["core.admin"],
            force=bool(options.get("force")),
            stdout=self.stdout,
            stderr=self.stderr,
        )
