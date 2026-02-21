from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Deprecated wrapper for the unified health command."""

    help = "[DEPRECATED] Use `manage.py health --target core.system_user`."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Create or repair the system account when issues are detected.",
        )

    def handle(self, *args, **options):
        self.stderr.write(
            self.style.WARNING(
                "check_system_user is deprecated; use `manage.py health --target core.system_user`."
            )
        )
        call_command(
            "health",
            target=["core.system_user"],
            force=bool(options.get("force")),
            stdout=self.stdout,
            stderr=self.stderr,
        )
