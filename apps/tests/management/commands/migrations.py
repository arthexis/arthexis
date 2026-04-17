"""Consolidated migration workflows and migration server helpers."""

from __future__ import annotations

import shlex
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from utils.qa_remediation import (
    emit_remediation,
    expected_venv_python,
    find_repo_root,
)


class Command(BaseCommand):
    """Run migration-related workflows from a unified command."""

    help = "Run migrate/makemigrations checks or start the migration server."

    def add_arguments(self, parser) -> None:
        """Configure CLI options and migration subcommands."""

        subparsers = parser.add_subparsers(dest="action")
        parser.set_defaults(
            action="run", database="default", app_label=None, migration_name=None
        )

        run_parser = subparsers.add_parser("run", help="Run Django migrations.")
        run_parser.add_argument("app_label", nargs="?")
        run_parser.add_argument(
            "migration_name",
            nargs="?",
            help="Optional migration target when app_label is provided.",
        )
        run_parser.add_argument("--database", default="default")

        make_parser = subparsers.add_parser("make", help="Run makemigrations.")
        make_parser.add_argument("app_labels", nargs="*")
        make_parser.add_argument("--check", action="store_true")
        make_parser.add_argument("--dry-run", action="store_true")

        subparsers.add_parser(
            "check",
            help="Run makemigrations --check --dry-run.",
        )

        server_parser = subparsers.add_parser(
            "server",
            help="Start the VS Code migration server watcher.",
        )
        server_parser.add_argument("--interval", type=float, default=1.0)
        server_parser.add_argument("--debounce", type=float, default=1.0)

    def handle(self, *args, **options) -> None:
        """Dispatch to the selected migration subcommand."""

        if not expected_venv_python(self._base_dir()).exists():
            raise CommandError(
                emit_remediation(
                    code="missing_venv_python",
                    command="./install.sh --terminal",
                    retry=self._retry_command_for_options(options),
                )
            )

        action = options["action"]
        if action == "run":
            self._run_migrate(options)
            return
        if action == "make":
            self._run_makemigrations(options)
            return
        if action == "check":
            self._run_check()
            return
        if action == "server":
            self._run_migration_server(options)
            return
        raise CommandError(f"Unsupported action: {action}")

    def _run_migrate(self, options: dict[str, object]) -> None:
        """Run Django ``migrate`` with optional target labels."""

        call_args: list[str] = []
        app_label = options.get("app_label")
        migration_name = options.get("migration_name")
        if isinstance(app_label, str) and app_label:
            call_args.append(app_label)
            if isinstance(migration_name, str) and migration_name:
                call_args.append(migration_name)
        database = options.get("database", "default")
        call_command("migrate", *call_args, database=database)

    def _run_makemigrations(self, options: dict[str, object]) -> None:
        """Run Django ``makemigrations`` with optional app labels."""

        app_labels = options.get("app_labels") or []
        check = bool(options.get("check"))
        dry_run = bool(options.get("dry_run"))
        call_command("makemigrations", *app_labels, check=check, dry_run=dry_run)

    def _run_check(self) -> None:
        """Run ``makemigrations --check --dry-run`` and fail when changes exist."""

        call_command("makemigrations", check=True, dry_run=True)

    def _run_migration_server(self, options: dict[str, object]) -> None:
        """Run the VS Code migration server watcher."""

        try:
            from utils.devtools import migration_server
        except ModuleNotFoundError as exc:
            raise CommandError(
                emit_remediation(
                    code="missing_dependency",
                    command="./env-refresh.sh --deps-only",
                    retry=self._retry_command_for_options(options),
                )
            ) from exc

        argv = [
            "--watch",
            "--interval",
            str(options["interval"]),
            "--debounce",
            str(options["debounce"]),
        ]
        exit_code = migration_server.main(argv)
        if exit_code != 0:
            raise CommandError(f"migration server exited with status {exit_code}")

    @staticmethod
    def _base_dir() -> "Path":
        """Return the repository root directory."""

        return find_repo_root(Path(__file__).resolve().parent)

    def _retry_command_for_options(self, options: dict[str, object]) -> str:
        """Build retry guidance for the current migration subcommand."""

        action = options.get("action") or "run"
        base_dir = self._base_dir()
        venv_rel = expected_venv_python(base_dir).relative_to(base_dir).as_posix()
        command: list[str] = [venv_rel, "manage.py", "migrations", str(action)]

        if action == "run":
            app_label = options.get("app_label")
            migration_name = options.get("migration_name")
            database = options.get("database", "default")
            if isinstance(app_label, str) and app_label:
                command.append(app_label)
                if isinstance(migration_name, str) and migration_name:
                    command.append(migration_name)
            if isinstance(database, str) and database:
                command.extend(["--database", database])
        elif action == "make":
            app_labels = options.get("app_labels")
            if isinstance(app_labels, list):
                command.extend(
                    label for label in app_labels if isinstance(label, str) and label
                )
            if options.get("check"):
                command.append("--check")
            if options.get("dry_run"):
                command.append("--dry-run")
        elif action == "server":
            interval = options.get("interval", 1.0)
            debounce = options.get("debounce", 1.0)
            command.extend(["--interval", str(interval), "--debounce", str(debounce)])

        return shlex.join(command)
