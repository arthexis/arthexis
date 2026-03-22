"""Unified migration maintenance command for local apps."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.exceptions import MigrationSchemaMissing
from django.db.utils import OperationalError


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
            help="Clear and regenerate app migrations, then tag initial migrations.",
        )
        rebuild_parser.add_argument(
            "--apps-dir",
            dest="apps_dir",
            help="Override the apps directory (defaults to settings.APPS_DIR)",
        )
        rebuild_parser.add_argument(
            "--branch-id",
            dest="branch_id",
            help="Stable identifier recorded by the branch tag operation.",
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
            branch_id = options["branch_id"] or f"rebuild-{datetime.now(timezone.utc):%Y%m%d%H%M%S}"
            self._rebuild_migrations(apps_dir, branch_id)
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

    def _rebuild_migrations(self, apps_dir: Path, branch_id: str) -> None:
        """Regenerate project migrations and tag new initial migrations."""

        if not apps_dir.exists():
            self.stderr.write(f"Apps directory not found: {apps_dir}")
            return

        project_apps = self._collect_project_apps(apps_dir)

        self._clear_migrations(apps_dir)
        call_command("makemigrations")

        tagged = self._tag_initial_migrations(apps_dir, branch_id, project_apps)
        if tagged:
            self.stdout.write("Tagged migrations with rebuild branch guards:")
            for path in tagged:
                self.stdout.write(f" - {path.relative_to(apps_dir)}")
        else:
            self.stdout.write(
                "No initial migrations were tagged; ensure makemigrations created them."
            )

    def _collect_project_apps(self, apps_dir: Path) -> list[str]:
        """Return local app labels that currently expose migrations packages."""

        return sorted(path.name for path in apps_dir.iterdir() if (path / "migrations").is_dir())

    def _tag_initial_migrations(self, apps_dir: Path, branch_id: str, project_apps: list[str]) -> list[Path]:
        """Add rebuild guards to regenerated initial migrations."""

        tagged: list[Path] = []
        for app_label in project_apps:
            migrations_dir = apps_dir / app_label / "migrations"
            if not migrations_dir.exists():
                continue

            initial_candidates = sorted(migrations_dir.glob("0001_*.py"))
            if not initial_candidates:
                continue

            target = initial_candidates[0]
            if self._inject_guard(target, branch_id, project_apps):
                tagged.append(target)
        return tagged

    def _inject_guard(self, migration_path: Path, branch_id: str, project_apps: list[str]) -> bool:
        """Insert a ``BranchTagOperation`` at the top of an initial migration."""

        content = migration_path.read_text(encoding="utf-8")
        if "BranchTagOperation" in content:
            return False

        import_hooks = (
            "from django.db import migrations, models",
            "from django.db import migrations",
        )
        guard_import = "from utils.migration_branches import BranchTagOperation"
        if guard_import not in content:
            for import_hook in import_hooks:
                if import_hook in content:
                    content = content.replace(import_hook, f"{import_hook}\n{guard_import}", 1)
                    break
            else:
                content = f"{guard_import}\n{content}"

        migration_label = f"{migration_path.parent.parent.name}.{migration_path.stem}"
        marker_match = re.search(r"^(?P<indent>\s*)operations\s*=\s*\[\s*$", content, re.MULTILINE)
        if not marker_match:
            raise ValueError(f"Could not find operations block in migration {migration_path}")
        marker = marker_match.group(0)
        indent = marker_match.group("indent")

        guard_line = (
            f"{indent}operations = [\n"
            f"{indent}    BranchTagOperation({json.dumps(branch_id)}, "
            f"migration_label={json.dumps(migration_label)}, "
            f"project_apps={tuple(project_apps)!r}),\n"
        )
        migration_path.write_text(content.replace(marker, guard_line, 1), encoding="utf-8")
        return True

    def _pending_migrations(self, database: str) -> None:
        """Report pending migration state with a single database round-trip.

        Parameters:
            database: Django database alias to inspect.

        Returns:
            None.

        Raises:
            CommandError: When the migration graph cannot be inspected.
        """

        connection = connections[database]
        try:
            executor = MigrationExecutor(connection)
            pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
        except (OperationalError, MigrationSchemaMissing) as exc:
            raise CommandError(f"Unable to inspect migration state for {database!r}: {exc}") from exc

        if pending:
            self.stdout.write("pending")
            return

        raise CommandError("no pending migrations")
