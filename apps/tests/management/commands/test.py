"""Consolidated test command with run and server subcommands."""

from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.tests.discovery import TestDiscoveryError, discover_suite_tests
from apps.tests.models import SuiteTest
from utils.python_env import resolve_project_python


class Command(BaseCommand):
    """Run local test workflows from a single command entrypoint."""

    help = "Run pytest or launch the long-running VS Code test server."

    def add_arguments(self, parser) -> None:
        """Register command arguments and subcommands."""

        subparsers = parser.add_subparsers(dest="action")
        parser.set_defaults(action="run", pytest_args=[])

        run_parser = subparsers.add_parser("run", help="Run pytest.")
        run_parser.add_argument(
            "pytest_args",
            nargs=argparse.REMAINDER,
            help="Arguments forwarded directly to pytest (use '-- ...').",
        )

        server_parser = subparsers.add_parser(
            "server",
            help="Start the VS Code test server watcher.",
        )

        subparsers.add_parser(
            "discover",
            help="Collect and refresh suite test metadata.",
        )

    def handle(self, *args, **options) -> None:
        """Dispatch to the selected subcommand."""

        action = options["action"]
        if action == "run":
            self._run_pytest(options.get("pytest_args", []))
            return
        if action == "server":
            self._run_test_server()
            return
        if action == "discover":
            self._discover_suite_tests()
            return
        raise CommandError(f"Unsupported action: {action}")

    def _run_pytest(self, pytest_args: list[str]) -> None:
        """Execute pytest as a subprocess."""

        if importlib.util.find_spec("pytest") is None:
            raise CommandError(
                "pytest is not installed in the active environment. "
                "Install test dependencies (for example: "
                "`.venv/bin/pip install -r requirements-ci.txt`) and retry."
            )

        args = list(pytest_args)
        if args and args[0] == "--":
            args = args[1:]
        command = [resolve_project_python(self._base_dir()), "-m", "pytest", *args]
        result = subprocess.run(command, cwd=self._base_dir(), env=os.environ.copy())
        if result.returncode != 0:
            raise CommandError(f"pytest exited with status {result.returncode}")

    def _run_test_server(self) -> None:
        """Start the long-running VS Code test server."""

        from utils.devtools import test_server

        exit_code = test_server.main([])
        if exit_code != 0:
            raise CommandError(f"test server exited with status {exit_code}")

    def _discover_suite_tests(self) -> None:
        """Collect pytest tests and persist metadata in ``SuiteTest`` rows."""

        try:
            tests = discover_suite_tests()
        except TestDiscoveryError as exc:
            raise CommandError(str(exc)) from exc

        with transaction.atomic():
            deleted_count, _deleted_details = SuiteTest.objects.all().delete()
            SuiteTest.objects.bulk_create([SuiteTest(**item) for item in tests])
        self.stdout.write(
            self.style.SUCCESS(
                f"Refreshed suite tests: removed {deleted_count}, discovered {len(tests)}."
            )
        )

    @staticmethod
    def _base_dir() -> Path:
        """Return the repository root directory."""

        path = Path(__file__).resolve().parent
        while path != path.parent:
            if (path / "manage.py").is_file() or (path / "pyproject.toml").is_file():
                return path
            path = path.parent
        raise FileNotFoundError("Repository root not found from command module path.")
