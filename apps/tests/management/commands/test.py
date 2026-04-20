"""Consolidated test command with run and server subcommands."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.tests.discovery import TestDiscoveryError, discover_suite_tests
from apps.tests.models import SuiteTest
from utils.python_env import resolve_project_python
from utils.qa_remediation import emit_remediation, expected_venv_python, find_repo_root


class Command(BaseCommand):
    """Run local test workflows from a single command entrypoint."""

    __test__ = False

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

        subparsers.add_parser(
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
        venv_python = expected_venv_python(base_dir)
        venv_rel = venv_python.relative_to(base_dir).as_posix()
        args = list(pytest_args)
        if args and args[0] == "--":
            args = args[1:]
        retry_command = f"{venv_rel} manage.py test run"
        if args:
            retry_command = f"{retry_command} -- {shlex.join(args)}"
        if not venv_python.exists():
            raise CommandError(
                emit_remediation(
                    code="missing_venv_python",
                    command="./install.sh --terminal",
                    retry=retry_command,
                )
            )
        python = resolve_project_python(base_dir)
        readiness = self._run_readiness_probe(base_dir, python)
        self._write_readiness_report(readiness)

        missing_dependencies = [
            dependency
            for dependency, available in readiness["dependencies"].items()
            if not available
        ]
        if missing_dependencies:
            raise CommandError(
                emit_remediation(
                    code="missing_dependency",
                    command="./env-refresh.sh --deps-only",
                    retry=retry_command,
                )
            )

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

        base_dir = self._base_dir()
        venv_rel = expected_venv_python(base_dir).relative_to(base_dir).as_posix()
        if not expected_venv_python(base_dir).exists():
            raise CommandError(
                emit_remediation(
                    code="missing_venv_python",
                    command="./install.sh --terminal",
                    retry=f"{venv_rel} manage.py test server",
                )
            )

        try:
            from utils.devtools import test_server

            exit_code = test_server.main([])
        except ModuleNotFoundError as exc:
            raise CommandError(
                emit_remediation(
                    code="missing_dependency",
                    command="./env-refresh.sh --deps-only",
                    retry=f"{venv_rel} manage.py test server",
                )
            ) from exc
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

        return find_repo_root(Path(__file__).resolve().parent)
