from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand

from apps.core.management.deprecation import absorbed_into_command


@absorbed_into_command("release check-pypi")
class Command(BaseCommand):
    """Deprecated wrapper for the unified release command."""

    help = "[DEPRECATED] Use `manage.py release check-pypi [release]`."

    def add_arguments(self, parser):
        """Register compatibility arguments."""

        parser.add_argument(
            "release",
            nargs="?",
            help=(
                "Release primary key or version to check. "
                "Defaults to the latest release for the active package."
            ),
        )

    def handle(self, *args, **options):
        """Delegate to ``release check-pypi`` while preserving legacy syntax."""

        self.stderr.write(
            self.style.WARNING(
                "check_pypi is deprecated; use `manage.py release check-pypi [release]`."
            )
        )
        args = ["release", "check-pypi"]
        if options.get("release"):
            args.append(options["release"])
        call_command(*args, stdout=self.stdout, stderr=self.stderr)
