"""Supported alias for ``release apply-migrations``."""

from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Delegate the alias entrypoint to ``release apply-migrations``."""

    help = "Alias for `release apply-migrations` (supported synonym)."

    def add_arguments(self, parser):
        """Register arguments accepted by the supported alias."""

        parser.add_argument("target_version", help="Target release version")
        parser.add_argument(
            "--installed-version",
            dest="installed_version",
            help="Installed release version. Defaults to VERSION file.",
        )
        parser.add_argument(
            "--bundle-dir",
            dest="bundle_dir",
            help="Bundle directory. Defaults to releases/<target_version>/migrations.",
        )
        parser.add_argument(
            "--strict",
            action="store_true",
            help="Fail instead of falling back to migrate when bundle verification fails.",
        )
        parser.add_argument(
            "--skip-data-transforms",
            action="store_true",
            help="Skip deferred post-migration data transforms.",
        )

    def handle(self, *args, **options):
        """Forward the alias command to the consolidated release CLI."""

        self.stdout.write("`apply_release_migrations` is a supported alias for `release apply-migrations`.")
        call_command(
            "release",
            "apply-migrations",
            options["target_version"],
            installed_version=options.get("installed_version"),
            bundle_dir=options.get("bundle_dir"),
            strict=bool(options.get("strict")),
            skip_data_transforms=bool(options.get("skip_data_transforms")),
            stdout=self.stdout,
            stderr=self.stderr,
        )
