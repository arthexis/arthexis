"""Unified migration maintenance command for local apps."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import re
from pathlib import Path
from typing import Any, Iterator

from django.apps import apps as django_apps
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.exceptions import MigrationSchemaMissing
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
        next_major_parser = subparsers.add_parser(
            "next-major-rebuild",
            help=(
                "Rebuild a clean migration branch for the next major version "
                "using per-app migration modules."
            ),
        )
        next_major_parser.add_argument(
            "--major-version",
            default=None,
            help="Target major version branch (default: 1.0).",
        )
        next_major_parser.add_argument(
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
            branch_id = options["branch_id"] or f"rebuild-{datetime.now(timezone.utc):%Y%m%d%H%M%S}"
            self._rebuild_migrations(apps_dir, branch_id)
            return

        if target == "next-major-rebuild":
            major_version = self._resolve_next_major_version(options.get("major_version"))
            self._rebuild_next_major_migrations(apps_dir, major_version)
            return

        raise CommandError(f"Unsupported migrations target: {target}")

    def _resolve_apps_dir(self, apps_dir_option: str | None) -> Path:
        return Path(apps_dir_option or getattr(settings, "APPS_DIR", Path(settings.BASE_DIR) / "apps"))

    def _tracks_file(self) -> Path:
        return Path(settings.BASE_DIR) / "MIGRATIONS.json"

    def _load_tracks(self) -> dict[str, Any]:
        tracks_file = self._tracks_file()
        if not tracks_file.exists():
            return {}
        try:
            data = json.loads(tracks_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CommandError(f"Invalid migration tracks file: {tracks_file}") from exc
        if isinstance(data, dict):
            return data
        raise CommandError(f"Migration tracks file must contain a JSON object: {tracks_file}")

    def _save_tracks(self, payload: dict[str, Any]) -> None:
        tracks_file = self._tracks_file()
        tracks_file.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _resolve_next_major_version(self, explicit_major: object | None) -> str:
        if explicit_major is not None and (version_str := str(explicit_major).strip()):
            return version_str
        tracks = self._load_tracks()
        next_major = tracks.get("next_major")
        if isinstance(next_major, dict):
            candidate = str(next_major.get("version", "")).strip()
            if candidate:
                return candidate
        return "1.0"

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
            CommandError: When the requested database alias is unavailable or the
                migration graph cannot be inspected for reasons other than an
                uninitialized migration schema.
        """

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

    def _rebuild_next_major_migrations(self, apps_dir: Path, major_version: str) -> None:
        """Rebuild the next-major migration line from scratch.

        This keeps current-version migrations untouched while regenerating
        clean baseline migrations in parallel modules dedicated to the next
        major release (for example, ``migrations_v1_0``).
        """

        if not apps_dir.exists():
            self.stderr.write(f"Apps directory not found: {apps_dir}")
            return

        project_apps = self._collect_project_apps(apps_dir)
        if not project_apps:
            self.stdout.write("No project apps with migrations found.")
            return

        major_slug = self._major_slug(major_version)
        migration_targets = self._collect_project_migration_targets(project_apps, apps_dir)
        migration_modules = self._build_track_modules(migration_targets, major_slug)
        target_dirs = self._prepare_track_dirs(apps_dir, migration_targets, major_slug)
        self._clear_track_migrations(target_dirs)

        with self._override_migration_modules(migration_modules):
            call_command("makemigrations")

        branch_id = f"major-{major_version}-base"
        tagged = self._tag_initial_migrations_for_modules(
            apps_dir=apps_dir,
            branch_id=branch_id,
            project_apps=project_apps,
            migration_modules=migration_modules,
            migration_targets=migration_targets,
        )
        self.stdout.write(
            f"Rebuilt next-major migration branch {major_version} ({major_slug})."
        )
        self._record_tracks_state(major_version=major_version, major_slug=major_slug)
        if tagged:
            self.stdout.write("Tagged next-major initial migrations:")
            for path in tagged:
                self.stdout.write(f" - {path.relative_to(apps_dir)}")
        else:
            self.stdout.write("No next-major initial migrations were tagged.")

    def _major_slug(self, major_version: str) -> str:
        normalized = re.sub(r"[^0-9.]", "", major_version).strip(".")
        if not normalized:
            raise CommandError("major-version must include numeric components")
        return f"v{normalized.replace('.', '_')}"

    def _collect_project_migration_targets(
        self, project_apps: list[str], apps_dir: Path
    ) -> dict[str, str]:
        app_label_overrides: dict[str, str] = {}
        for app_config in django_apps.get_app_configs():
            if not app_config.name.startswith("apps."):
                continue
            try:
                app_path = Path(app_config.path).resolve()
            except FileNotFoundError:
                continue
            try:
                relative_path = app_path.relative_to(apps_dir.resolve())
            except ValueError:
                continue
            if len(relative_path.parts) != 1:
                continue
            app_label_overrides[relative_path.name] = app_config.label

        migration_targets: dict[str, str] = {}
        for package_name in project_apps:
            app_label = app_label_overrides.get(package_name, package_name)
            migration_targets[app_label] = package_name
        return migration_targets

    def _build_track_modules(
        self, migration_targets: dict[str, str], major_slug: str
    ) -> dict[str, str]:
        return {
            app_label: f"apps.{package_name}.migrations_{major_slug}"
            for app_label, package_name in migration_targets.items()
        }

    def _prepare_track_dirs(
        self, apps_dir: Path, migration_targets: dict[str, str], major_slug: str
    ) -> list[Path]:
        dirs: list[Path] = []
        for package_name in migration_targets.values():
            migrations_dir = apps_dir / package_name / f"migrations_{major_slug}"
            migrations_dir.mkdir(parents=True, exist_ok=True)
            (migrations_dir / "__init__.py").touch()
            dirs.append(migrations_dir)
        return dirs

    def _clear_track_migrations(self, migration_dirs: list[Path]) -> None:
        for migrations_dir in migration_dirs:
            for migration_file in migrations_dir.rglob("*.py"):
                if migration_file.name == "__init__.py":
                    continue
                migration_file.unlink(missing_ok=True)

    @contextmanager
    def _override_migration_modules(
        self, migration_modules: dict[str, str]
    ) -> Iterator[None]:
        had_attr = hasattr(settings, "MIGRATION_MODULES")
        previous = dict(getattr(settings, "MIGRATION_MODULES", {}))
        merged = {**previous, **migration_modules}
        settings.MIGRATION_MODULES = merged
        try:
            yield
        finally:
            if had_attr:
                settings.MIGRATION_MODULES = previous
            else:
                delattr(settings, "MIGRATION_MODULES")

    def _tag_initial_migrations_for_modules(
        self,
        *,
        apps_dir: Path,
        branch_id: str,
        project_apps: list[str],
        migration_modules: dict[str, str],
        migration_targets: dict[str, str],
    ) -> list[Path]:
        tagged: list[Path] = []
        for app_label, module_path in migration_modules.items():
            suffix = module_path.split(".")[-1]
            package_name = migration_targets[app_label]
            migrations_dir = apps_dir / package_name / suffix
            if not migrations_dir.exists():
                continue

            initial_candidates = sorted(migrations_dir.glob("0001_*.py"))
            if not initial_candidates:
                continue

            target = initial_candidates[0]
            if self._inject_guard(target, branch_id, project_apps):
                tagged.append(target)
        return tagged

    def _record_tracks_state(self, *, major_version: str, major_slug: str) -> None:
        tracks = self._load_tracks()
        tracks["current_version"] = self._read_repo_version()
        tracks["current_line"] = "0.x"
        tracks["next_major"] = {
            "version": major_version,
            "status": "rebuild",
            "module_suffix": f"migrations_{major_slug}",
        }
        self._save_tracks(tracks)

    def _read_repo_version(self) -> str:
        version_path = Path(settings.BASE_DIR) / "VERSION"
        if version_path.exists():
            return version_path.read_text(encoding="utf-8").strip()
        return ""
