"""Consolidated migration workflows and migration server helpers."""

from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError


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
        server_parser.add_argument(
            "--latest",
            dest="latest",
            action="store_true",
            default=True,
            help="Pass --latest to env-refresh (default).",
        )
        server_parser.add_argument(
            "--no-latest",
            dest="latest",
            action="store_false",
            help="Do not pass --latest to env-refresh.",
        )

    def handle(self, *args, **options) -> None:
        """Dispatch to the selected migration subcommand."""

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

        from utils.devtools import migration_server

        argv = ["--interval", str(options["interval"])]
        argv.append("--latest" if options["latest"] else "--no-latest")
        exit_code = migration_server.main(argv)
        if exit_code != 0:
            raise CommandError(f"migration server exited with status {exit_code}")
