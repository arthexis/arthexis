from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.release.domain import capture_migration_state


class Command(BaseCommand):
    help = "Capture migration plan and schema artifacts for a release"

    def add_arguments(self, parser):
        parser.add_argument("version", help="Release version to snapshot")

    def handle(self, *args, **options):
        version: str = options["version"]
        try:
            out_dir = capture_migration_state(version)
        except Exception as exc:  # pragma: no cover - orchestration wrapper
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"Captured migration state in {out_dir}"))
