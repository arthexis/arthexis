"""Unified migration maintenance command for local apps."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from django.apps import apps as django_apps
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.db.migrations.exceptions import AmbiguityError, MigrationSchemaMissing
from django.db.migrations.executor import MigrationExecutor
from django.db.utils import OperationalError
from django.utils.connection import ConnectionDoesNotExist

from scripts.check_migration_conflicts import (
    MigrationCheckError,
    build_migration_impact_report,
    format_migration_impact_markdown,
)


class Command(BaseCommand):
    """Run migration maintenance workflows for project-local apps."""

    help = (
        "Run migration maintenance workflows "
        "(check, pending, benchmark, clear, rebuild) for apps.* packages."
    )

    def add_arguments(self, parser):
        """Register subcommands for migration maintenance tasks."""

        subparsers = parser.add_subparsers(dest="target")
        subparsers.required = True

        subparsers.add_parser(
            "check",
            help="Run makemigrations --check --dry-run.",
        )
        impact_parser = subparsers.add_parser(
            "impact",
            help="Report migration impact for changed migration files.",
        )
        impact_parser.add_argument(
            "--base-ref",
            default="origin/main",
            help="Base ref used for changed-file discovery (default: origin/main).",
        )
        impact_parser.add_argument(
            "--format",
            choices=("json", "markdown"),
            default="markdown",
            help="Report output format.",
        )
        impact_parser.add_argument(
            "--output",
            help="Optional report output path; relative paths resolve from BASE_DIR.",
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
        benchmark_parser = subparsers.add_parser(
            "benchmark",
            help="Benchmark migration planning and optional execution.",
        )
        benchmark_parser.add_argument(
            "app_label",
            nargs="?",
            help="Optional app label to benchmark.",
        )
        benchmark_parser.add_argument(
            "migration_name",
            nargs="?",
            help="Optional target migration name for the selected app.",
        )
        benchmark_parser.add_argument(
            "--database",
            default="default",
            help="Database alias used for migration planning and execution.",
        )
        benchmark_parser.add_argument(
            "--apply",
            action="store_true",
            help="Run migrate after timing the plan. Default is plan-only.",
        )
        benchmark_parser.add_argument(
            "--output",
            help="Benchmark JSON output path (default: work/migration-benchmark-<run-id>.json).",
        )
        benchmark_parser.add_argument(
            "--run-id",
            help="Benchmark run identifier used for the default output path.",
        )
        benchmark_parser.add_argument(
            "--json",
            action="store_true",
            help="Emit the benchmark report JSON after completion.",
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

        if target == "impact":
            self._migration_impact(options)
            return

        if target == "pending":
            self._pending_migrations(options["database"])
            return

        if target == "benchmark":
            self._benchmark_migrations(options)
            return

        if target == "rebuild":
            self._rebuild_migrations(apps_dir)
            return

        raise CommandError(f"Unsupported migrations target: {target}")

    def _resolve_apps_dir(self, apps_dir_option: str | None) -> Path:
        return Path(
            apps_dir_option
            or getattr(settings, "APPS_DIR", Path(settings.BASE_DIR) / "apps")
        )

    def _check_migrations(self) -> None:
        """Run Django's pending-migration detection without writing files."""

        call_command("makemigrations", check=True, dry_run=True)

    def _migration_impact(self, options: dict[str, Any]) -> None:
        """Write migration impact for the current branch."""

        try:
            payload = build_migration_impact_report(
                Path(settings.BASE_DIR),
                base_ref=options["base_ref"],
            )
        except MigrationCheckError as exc:
            raise CommandError(str(exc)) from exc

        if options["format"] == "json":
            rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        else:
            rendered = format_migration_impact_markdown(payload)

        output = options.get("output")
        if output:
            output_path = Path(output)
            if not output_path.is_absolute():
                output_path = Path(settings.BASE_DIR) / output_path
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(rendered, encoding="utf-8")

        self.stdout.write(rendered, ending="")

    def _get_project_app_labels(self, apps_dir: Path) -> list[str]:
        """Collect Django app labels rooted under the configured apps directory."""

        root = apps_dir.resolve()
        labels = []
        for app_config in django_apps.get_app_configs():
            app_path = Path(app_config.path).resolve()
            if app_path == root or root in app_path.parents:
                labels.append(app_config.label)
        return sorted(labels)

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

        project_apps = self._get_project_app_labels(apps_dir)
        self._clear_migrations(apps_dir)
        call_command("makemigrations", *project_apps)

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

    def _benchmark_migrations(self, options: dict[str, Any]) -> None:
        """Benchmark migration planning and optionally apply the same target."""

        database = options["database"]
        app_label = options.get("app_label")
        migration_name = options.get("migration_name")
        output_path = self._benchmark_output_path(
            output=options.get("output"),
            run_id=options.get("run_id"),
        )

        try:
            connection = connections[database]
            plan_started_at = time.perf_counter()
            executor = MigrationExecutor(connection)
            self._benchmark_preflight(executor=executor, connection=connection)
            targets = self._benchmark_targets(
                executor=executor,
                app_label=app_label,
                migration_name=migration_name,
            )
            plan = executor.migration_plan(targets)
            planning_duration = time.perf_counter() - plan_started_at
        except ConnectionDoesNotExist as exc:
            raise CommandError(
                f"Unable to benchmark migration state for {database!r}: {exc}"
            ) from exc

        payload = {
            "schema_version": 1,
            "tool": "django-migrations",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "database": {
                "alias": database,
                "vendor": getattr(connection, "vendor", "unknown"),
            },
            "target": {
                "app_label": app_label,
                "migration_name": migration_name,
                "targets": [
                    {"app_label": target[0], "migration_name": target[1]}
                    for target in targets
                ],
            },
            "planning": {
                "duration_seconds": round(planning_duration, 6),
                "pending_count": len(plan),
                "migrations": self._migration_plan_payload(plan),
            },
            "execution": {
                "mode": "apply" if options.get("apply") else "plan-only",
                "applied": False,
                "duration_seconds": 0.0,
                "planned_count": len(plan),
            },
        }

        if options.get("apply"):
            execute_started_at = time.perf_counter()
            call_command(
                "migrate",
                *self._migrate_args(app_label, migration_name),
                database=database,
                interactive=False,
                verbosity=0,
            )
            payload["execution"] = {
                "mode": "apply",
                "applied": True,
                "duration_seconds": round(time.perf_counter() - execute_started_at, 6),
                "planned_count": len(plan),
            }

        output_path.parent.mkdir(parents=True, exist_ok=True)
        payload["report_path"] = str(output_path)
        output_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
        )

        if options.get("json"):
            self.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
        else:
            self._write_benchmark_summary(payload)

    def _benchmark_targets(
        self,
        *,
        executor: MigrationExecutor,
        app_label: str | None,
        migration_name: str | None,
    ) -> list[tuple[str, str]]:
        if app_label and migration_name:
            if app_label not in executor.loader.migrated_apps:
                raise CommandError(f"Unknown app label: {app_label}")
            if migration_name == "zero":
                return [(app_label, None)]
            try:
                migration = executor.loader.get_migration_by_prefix(
                    app_label, migration_name
                )
            except AmbiguityError as error:
                raise CommandError(str(error)) from error
            except KeyError as error:
                raise CommandError(
                    f"Unknown migration target: {app_label} {migration_name}"
                ) from error
            target = (app_label, migration.name)
            if (
                target not in executor.loader.graph.nodes
                and target in executor.loader.replacements
            ):
                target = executor.loader.replacements[target].replaces[-1]
            return [target]
        if app_label:
            if app_label not in executor.loader.migrated_apps:
                raise CommandError(f"Unknown app label: {app_label}")
            return list(executor.loader.graph.leaf_nodes(app_label))
        return list(executor.loader.graph.leaf_nodes())

    def _migration_plan_payload(
        self, plan: list[tuple[Any, bool]]
    ) -> list[dict[str, Any]]:
        return [
            {
                "app_label": migration.app_label,
                "migration_name": migration.name,
                "backwards": bool(backwards),
            }
            for migration, backwards in plan
        ]

    def _write_benchmark_summary(self, payload: dict[str, Any]) -> None:
        planning = payload["planning"]
        execution = payload["execution"]
        database = payload["database"]
        self.stdout.write("Migration benchmark summary:")
        self.stdout.write(f"- report: {payload['report_path']}")
        self.stdout.write(f"- database: {database['alias']} ({database['vendor']})")
        self.stdout.write(
            "- planning: "
            f"{planning['pending_count']} pending in "
            f"{planning['duration_seconds']:.3f}s"
        )
        if execution["applied"]:
            self.stdout.write(
                f"- execution: applied in {execution['duration_seconds']:.3f}s"
            )
        else:
            self.stdout.write("- execution: skipped (plan-only)")

    @staticmethod
    def _benchmark_preflight(*, executor: MigrationExecutor, connection: Any) -> None:
        executor.loader.check_consistent_history(connection)
        conflicts = executor.loader.detect_conflicts()
        if conflicts:
            name_str = "; ".join(
                "{} in {}".format(", ".join(names), app_label)
                for app_label, names in conflicts.items()
            )
            raise CommandError(
                "Conflicting migrations detected; multiple leaf nodes in the "
                f"migration graph: ({name_str}).\n"
                "To fix them run 'python manage.py makemigrations --merge'"
            )

    @staticmethod
    def _benchmark_output_path(*, output: str | None, run_id: str | None) -> Path:
        if output:
            output_path = Path(output)
            if not output_path.is_absolute():
                output_path = Path(settings.BASE_DIR) / output_path
            return output_path
        identifier = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        identifier_path = Path(identifier)
        if (
            identifier_path.is_absolute()
            or identifier_path.name != identifier
            or identifier_path.name in {"", ".", ".."}
        ):
            raise CommandError(
                "Invalid run ID: path traversal or subdirectories are not allowed "
                "in --run-id."
            )
        return (
            Path(settings.BASE_DIR) / "work" / f"migration-benchmark-{identifier}.json"
        )

    @staticmethod
    def _migrate_args(app_label: str | None, migration_name: str | None) -> list[str]:
        if app_label and migration_name:
            return [app_label, migration_name]
        if app_label:
            return [app_label]
        return []
