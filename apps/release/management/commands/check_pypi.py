from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Deprecated wrapper for the unified health command."""

    help = "[DEPRECATED] Use `manage.py health --target release.pypi`."

    def add_arguments(self, parser):
        parser.add_argument(
            "release",
            nargs="?",
            help=(
                "Release primary key or version to check. "
                "Defaults to the latest release for the active package."
            ),
        )

    def handle(self, *args, **options):
        self.stderr.write(
            self.style.WARNING(
                "check_pypi is deprecated; use `manage.py health --target release.pypi`."
            )
        )
        call_command(
            "health",
            target=["release.pypi"],
            release=options.get("release"),
            stdout=self.stdout,
            stderr=self.stderr,
        )
