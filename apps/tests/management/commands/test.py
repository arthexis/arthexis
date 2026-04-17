"""Consolidated test command with run and server subcommands."""

from __future__ import annotations

import argparse
import json
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

    help = "Run suite tests via the canonical manage.py entrypoint or launch the VS Code test server."

    def add_arguments(self, parser) -> None:
        """Register command arguments and subcommands."""

        subparsers = parser.add_subparsers(dest="action")
        parser.set_defaults(action="run", pytest_args=[])

        run_parser = subparsers.add_parser("run", help="Run tests (pytest-backed).")
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

        base_dir = self._base_dir()
        python = resolve_project_python(base_dir)
        readiness = self._run_readiness_probe(base_dir, python)
        self._write_readiness_report(readiness)

        missing_dependencies = [
            dependency
            for dependency, available in readiness["dependencies"].items()
            if not available
        ]
        if missing_dependencies:
            dependency_list = ", ".join(missing_dependencies)
            raise CommandError(
                "Core test dependencies are missing in the active environment: "
                f"{dependency_list}. Install QA dependencies (for example: "
                "`.venv/bin/pip install '.[qa]'`) and retry."
            )

        args = list(pytest_args)
        if args and args[0] == "--":
            args = args[1:]
        command = [python, "-m", "pytest", *args]
        result = subprocess.run(command, cwd=base_dir, env=os.environ.copy())
        if result.returncode != 0:
            raise CommandError(f"pytest exited with status {result.returncode}")

    def _run_readiness_probe(self, base_dir: Path, python: str) -> dict[str, object]:
        """Collect QA readiness details from the selected Python interpreter."""

        probe = subprocess.run(
            [
                python,
                "-c",
                (
                    "import importlib.util,json,os,sys;"
                    "deps={'pytest':'pytest','pytest-django':'pytest_django',"
                    "'pytest-timeout':'pytest_timeout'};"
                    "print(json.dumps({"
                    "'python_executable':sys.executable,"
                    "'virtualenv_active':bool(os.environ.get('VIRTUAL_ENV')) "
                    "or sys.prefix!=getattr(sys,'base_prefix',sys.prefix),"
                    "'virtualenv_path':os.environ.get('VIRTUAL_ENV'),"
                    "'dependencies':{pkg:bool(importlib.util.find_spec(mod)) "
                    "for pkg, mod in deps.items()}}))"
                ),
            ],
            capture_output=True,
            check=False,
            cwd=base_dir,
            env=os.environ.copy(),
            text=True,
        )
        if probe.returncode != 0:
            raise CommandError("Unable to run QA readiness probe for test execution.")
        try:
            lines = probe.stdout.strip().splitlines()
            if not lines:
                raise CommandError("QA readiness probe produced no output.")
            readiness = json.loads(lines[-1])
        except json.JSONDecodeError as exc:
            raise CommandError("QA readiness probe returned invalid output.") from exc
        if not isinstance(readiness, dict):
            raise CommandError("QA readiness probe returned malformed output.")
        return readiness

    def _write_readiness_report(self, readiness: dict[str, object]) -> None:
        """Print a compact readiness report before any pytest execution."""

        dependencies = readiness.get("dependencies", {})
        if not isinstance(dependencies, dict):
            dependencies = {}
        dependency_report = ", ".join(
            f"{dependency}={'yes' if bool(available) else 'no'}"
            for dependency, available in dependencies.items()
        )
        if not dependency_report:
            dependency_report = "none detected"

        self.stdout.write("QA readiness:")
        self.stdout.write(
            f"- virtualenv active: {'yes' if readiness.get('virtualenv_active') else 'no'}"
        )
        self.stdout.write(
            f"- python executable: {readiness.get('python_executable', 'unknown')}"
        )
        self.stdout.write(f"- core test dependencies: {dependency_report}")

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
