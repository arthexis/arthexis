from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Remove all migration files under the ``apps`` package."""

    help = "Remove all apps.* migration files (except __init__.py)."

    def handle(self, *args, **options):
        apps_dir = Path(getattr(settings, "APPS_DIR", Path(settings.BASE_DIR) / "apps"))
        if not apps_dir.exists():
            self.stderr.write(f"Apps directory not found: {apps_dir}")
            return

        removed_files: list[Path] = []

        for migrations_dir in apps_dir.glob("*/migrations"):
            if not migrations_dir.is_dir():
                continue

            for migration_file in migrations_dir.rglob("*.py"):
                if migration_file.name == "__init__.py":
                    continue

                migration_file.unlink(missing_ok=True)
                removed_files.append(migration_file)

        if removed_files:
            self.stdout.write("Removed migrations:")
            for path in sorted(removed_files):
                self.stdout.write(f" - {path.relative_to(apps_dir)}")
        else:
            self.stdout.write("No migration files found to remove.")
