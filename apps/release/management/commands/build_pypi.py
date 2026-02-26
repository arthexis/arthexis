from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand

from apps.core.management.deprecation import absorbed_into_command


@absorbed_into_command("release build")
class Command(BaseCommand):
    """Deprecated wrapper for the unified release command."""

    help = "[DEPRECATED] Use `manage.py release build ...`."

    def add_arguments(self, parser):
        """Register compatibility arguments."""

        parser.add_argument("--bump", action="store_true", help="Increment patch version")
        parser.add_argument("--dist", action="store_true", help="Build distribution")
        parser.add_argument("--twine", action="store_true", help="Upload with Twine")
        parser.add_argument("--git", action="store_true", help="Commit and push changes")
        parser.add_argument("--tag", action="store_true", help="Create and push a git tag")
        parser.add_argument("--test", action="store_true", help="Run tests before building")
        parser.add_argument(
            "--all", action="store_true", help="Enable bump, dist, twine, git and tag"
        )
        parser.add_argument("--force", action="store_true", help="Skip PyPI version check")
        parser.add_argument(
            "--stash", action="store_true", help="Auto stash changes before building"
        )
        parser.add_argument("--package", help="Build using the specified package (ID or name)")

    def handle(self, *args, **options):
        """Delegate to ``release build`` while preserving legacy flags."""

        self.stderr.write(
            self.style.WARNING(
                "build_pypi is deprecated; use `manage.py release build` with matching flags."
            )
        )
        return call_command(
            "release",
            "build",
            bump=options["bump"],
            dist=options["dist"],
            twine=options["twine"],
            git=options["git"],
            tag=options["tag"],
            test=options["test"],
            all=options["all"],
            force=options["force"],
            stash=options["stash"],
            package=options.get("package"),
            stdout=self.stdout,
            stderr=self.stderr,
        )
