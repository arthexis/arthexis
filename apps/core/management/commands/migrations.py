"""Unified migration maintenance command for local apps."""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.db.migrations.exceptions import MigrationSchemaMissing
from django.db.migrations.executor import MigrationExecutor
from django.db.utils import OperationalError
from django.utils.connection import ConnectionDoesNotExist


class Command(BaseCommand):
    """Run migration maintenance workflows for project-local apps."""

    help = (
        "Run migration maintenance workflows "
        "(check, clear, rebuild) for apps.* packages."
    )

    def add_arguments(self, parser):
        """Register subcommands for migration maintenance tasks."""

        subparsers = parser.add_subparsers(dest="target")
        subparsers.required = True

        subparsers.add_parser(
            "check",
            help="Run makemigrations --check --dry-run.",
        )
        pending_parser = subparsers.add_parser(
            "pending",
            help="Exit successfully when unapplied migrations exist.",
        )
        pending_parser.add_argument(
            "--database",
            default="default",
            help="Database alias used for pending-migration detection.",
        )

        clear_parser = subparsers.add_parser(
            "clear", help="Remove all app migration files except __init__.py."
        )
        clear_parser.add_argument(
            "--apps-dir",
            dest="apps_dir",
            help="Override the apps directory (defaults to settings.APPS_DIR)",
        )

        rebuild_parser = subparsers.add_parser(
            "rebuild",
            help="Clear and regenerate app migrations.",
        )
        rebuild_parser.add_argument(
            "--apps-dir",
            dest="apps_dir",
            help="Override the apps directory (defaults to settings.APPS_DIR)",
        )

    def handle(self, *args, **options):
        """Dispatch migration operations."""

        target = options["target"]
        apps_dir = self._resolve_apps_dir(options.get("apps_dir"))

        if target == "check":
            self._check_migrations()
            return

        if target == "clear":
            self._clear_migrations(apps_dir)
            return

        if target == "pending":
            self._pending_migrations(options["database"])
            return

        if target == "rebuild":
            self._rebuild_migrations(apps_dir)
            return

        raise CommandError(f"Unsupported migrations target: {target}")

    def _resolve_apps_dir(self, apps_dir_option: str | None) -> Path:
        return Path(apps_dir_option or getattr(settings, "APPS_DIR", Path(settings.BASE_DIR) / "apps"))

    def _check_migrations(self) -> None:
        """Run Django's pending-migration detection without writing files."""

        call_command("makemigrations", check=True, dry_run=True)

    def _clear_migrations(self, apps_dir: Path) -> None:
        """Remove generated migration modules while keeping package markers."""

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

    def _rebuild_migrations(self, apps_dir: Path) -> None:
        """Regenerate project migrations from a clean baseline."""

        if not apps_dir.exists():
            self.stderr.write(f"Apps directory not found: {apps_dir}")
            return

        self._clear_migrations(apps_dir)
        call_command("makemigrations")

    def _pending_migrations(self, database: str) -> None:
        """Report pending migration state with a single database round-trip."""

        try:
            connection = connections[database]
            executor = MigrationExecutor(connection)
            pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
        except ConnectionDoesNotExist as exc:
            raise CommandError(
                f"Unable to inspect migration state for {database!r}: {exc}"
            ) from exc
        except (OperationalError, MigrationSchemaMissing):
            pending = [database]

        if pending:
            self.stdout.write("pending")
            return

        raise CommandError("no pending migrations")
