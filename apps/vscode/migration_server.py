#!/usr/bin/env python3
"""Run Django migrations once for VS Code launcher workflows."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Protocol

BASE_DIR = Path(__file__).resolve().parents[2]
PREFIX = "[Migration Runner]"
CONFLICT_PATTERN = re.compile(
    r"Conflicting migrations detected; multiple leaf nodes in the migration graph",
    re.IGNORECASE,
)


class ProcessLike(Protocol):
    """Protocol for subprocess handles used by the VS Code migration launcher."""

    def communicate(self) -> tuple[str, str]:
        """Return buffered stdout and stderr for the completed process."""

    def wait(self) -> int:
        """Wait for the process and return its exit code."""

    def terminate(self) -> None:
        """Request a graceful process termination."""

    def kill(self) -> None:
        """Forcefully terminate the process."""


class CommandResult(Protocol):
    """Protocol describing completed command output."""

    returncode: int
    stdout: str
    stderr: str


def build_migration_command(extra_args: list[str] | None = None) -> list[str]:
    """Build the command used to execute Django migrations."""

    command = [sys.executable, "manage.py", "migrate"]
    if extra_args:
        command.extend(extra_args)
    return command


def build_merge_command() -> list[str]:
    """Build the command used to generate Django merge migrations."""

    return [sys.executable, "manage.py", "makemigrations", "--merge", "--noinput"]


def _build_popen_kwargs() -> dict[str, object]:
    """Build common subprocess arguments for migration-related commands."""

    popen_kwargs: dict[str, object] = {
        "cwd": BASE_DIR,
        "env": _build_subprocess_env(),
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
    }

    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", None)
    if sys.platform == "win32" and creationflags is not None:
        # Keep migration subprocess in its own process group so debugger-level
        # Ctrl+C signals do not interrupt import-time startup unexpectedly.
        popen_kwargs["creationflags"] = creationflags
    elif sys.platform != "win32":
        # On POSIX, start a new session to avoid debugger-level Ctrl+C signals
        # propagating into this subprocess during startup.
        popen_kwargs["start_new_session"] = True

    return popen_kwargs


def _build_subprocess_env() -> dict[str, str]:
    """Build subprocess environment with debug mode disabled for migrations."""

    env = dict(os.environ)
    env["DEBUG"] = "0"
    env["DJANGO_DEBUG"] = "0"
    return env


def _collect_process_result(process: ProcessLike) -> CommandResult:
    """Collect process output into a ``CompletedProcess``-compatible object."""

    stdout, stderr = process.communicate()
    returncode = process.wait()
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def _stop_interrupted_process(process: ProcessLike) -> None:
    """Stop an interrupted process, escalating to force-kill when needed."""

    process.terminate()
    try:
        process.wait()
    except KeyboardInterrupt:
        process.kill()
        process.wait()


def _run_command(command: list[str]) -> CommandResult | None:
    """Run a command while preserving useful console output and signal handling."""

    print(f"{PREFIX} Running: {' '.join(command)}")
    process: ProcessLike = subprocess.Popen(command, **_build_popen_kwargs())
    try:
        completed = _collect_process_result(process)
    except KeyboardInterrupt:
        print(f"{PREFIX} Interrupted. Stopping migration process...")
        _stop_interrupted_process(process)
        return None

    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)
    return completed


def _has_conflicting_migrations(completed: CommandResult) -> bool:
    """Return ``True`` when command output indicates migration graph conflicts."""

    output = f"{completed.stdout}\n{completed.stderr}"
    return bool(CONFLICT_PATTERN.search(output))


def run_migrations(extra_args: list[str] | None = None) -> int:
    """Run ``manage.py migrate`` and return the subprocess exit code."""

    completed = _run_command(build_migration_command(extra_args))
    if completed is None:
        return 130

    if completed.returncode != 0 and _has_conflicting_migrations(completed):
        print(f"{PREFIX} Conflicting migrations detected. Attempting automatic merge.")
        merge_completed = _run_command(build_merge_command())
        if merge_completed is None:
            return 130
        if merge_completed.returncode != 0:
            print(f"{PREFIX} Automatic merge failed with exit code {merge_completed.returncode}.")
            return merge_completed.returncode

        print(f"{PREFIX} Merge completed. Re-running migrations.")
        completed = _run_command(build_migration_command(extra_args))
        if completed is None:
            return 130

    if completed.returncode == 0:
        print(f"{PREFIX} Migrations completed successfully.")
    else:
        print(f"{PREFIX} Migrations failed with exit code {completed.returncode}.")
    return completed.returncode


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse launcher arguments for a one-shot migration run."""

    parser = argparse.ArgumentParser(
        description="Run Django migrations once."
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Compatibility flag from legacy watcher mode (ignored).",
    )
    parser.add_argument(
        "--debounce",
        type=float,
        default=1.0,
        help="Compatibility flag from legacy watcher mode (ignored).",
    )
    parser.add_argument(
        "--latest",
        dest="latest",
        action="store_true",
        default=True,
        help="Compatibility flag from legacy watcher mode (ignored).",
    )
    parser.add_argument(
        "--no-latest",
        dest="latest",
        action="store_false",
        help="Compatibility flag from legacy watcher mode (ignored).",
    )
    parser.add_argument(
        "extra_args",
        nargs=argparse.REMAINDER,
        help="Additional args passed to `manage.py migrate`.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for VS Code migration launcher tasks."""

    args = parse_args(argv)
    extra_args = args.extra_args
    if extra_args and extra_args[0] == "--":
        extra_args = extra_args[1:]
    return run_migrations(extra_args)


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
