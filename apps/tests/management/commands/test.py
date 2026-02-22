"""Consolidated test command with run and server subcommands."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    """Run local test workflows from a single command entrypoint."""

    help = "Run pytest or launch the long-running VS Code test server."

    def add_arguments(self, parser) -> None:
        """Register command arguments and subcommands."""

        subparsers = parser.add_subparsers(dest="action")
        parser.set_defaults(action="run")

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
        server_parser.add_argument("--interval", type=float, default=1.0)
        server_parser.add_argument("--debounce", type=float, default=1.0)
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
        """Dispatch to the selected subcommand."""

        action = options["action"]
        if action == "run":
            self._run_pytest(options["pytest_args"])
            return
        if action == "server":
            self._run_test_server(
                interval=options["interval"],
                debounce=options["debounce"],
                latest=options["latest"],
            )
            return
        raise CommandError(f"Unsupported action: {action}")

    def _run_pytest(self, pytest_args: list[str]) -> None:
        """Execute pytest as a subprocess."""

        args = list(pytest_args)
        if args and args[0] == "--":
            args = args[1:]
        command = [sys.executable, "-m", "pytest", *args]
        result = subprocess.run(command, cwd=self._base_dir(), env=os.environ.copy())
        if result.returncode != 0:
            raise CommandError(f"pytest exited with status {result.returncode}")

    def _run_test_server(self, *, interval: float, debounce: float, latest: bool) -> None:
        """Start the long-running VS Code test server."""

        from apps.vscode import test_server

        argv = ["--interval", str(interval), "--debounce", str(debounce)]
        argv.append("--latest" if latest else "--no-latest")
        exit_code = test_server.main(argv)
        if exit_code != 0:
            raise CommandError(f"test server exited with status {exit_code}")

    @staticmethod
    def _base_dir() -> Path:
        """Return the repository root directory."""

        return Path(__file__).resolve().parents[4]
