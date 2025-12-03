from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.release.domain import prepare_release


class Command(BaseCommand):
    help = "Prepare a release using the release domain helpers"

    def add_arguments(self, parser):
        parser.add_argument("version", help="Version string for the release")

    def handle(self, *args, **options):
        version: str = options["version"]
        try:
            prepare_release(version)
        except Exception as exc:  # pragma: no cover - orchestration wrapper
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"Release {version} prepared"))
