#!/usr/bin/env python3
"""Run Django migrations once for VS Code launcher workflows."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
PREFIX = "[Migration Runner]"
CONFLICT_PATTERN = re.compile(
    r"Conflicting migrations detected; multiple leaf nodes in the migration graph",
    re.IGNORECASE,
)


def build_migration_command(extra_args: list[str] | None = None) -> list[str]:
    """Build the command used to execute Django migrations."""

    command = [sys.executable, "manage.py", "migrate"]
    if extra_args:
        command.extend(extra_args)
    return command


def build_merge_command() -> list[str]:
    """Build the command used to generate Django merge migrations."""

    return [sys.executable, "manage.py", "makemigrations", "--merge", "--noinput"]


def _build_run_kwargs() -> dict[str, object]:
    """Build common subprocess arguments for migration-related commands."""

    run_kwargs: dict[str, object] = {
        "cwd": BASE_DIR,
        "check": False,
        "capture_output": True,
        "text": True,
    }

    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", None)
    if sys.platform == "win32" and creationflags is not None:
        # Keep migration subprocess in its own process group so debugger-level
        # Ctrl+C signals do not interrupt import-time startup unexpectedly.
        run_kwargs["creationflags"] = creationflags
    elif sys.platform != "win32":
        # On POSIX, start a new session to avoid debugger-level Ctrl+C signals
        # propagating into this subprocess during startup.
        run_kwargs["start_new_session"] = True

    return run_kwargs


def _run_command(command: list[str]) -> subprocess.CompletedProcess[str] | None:
    """Run a command while preserving useful console output and signal handling."""

    print(f"{PREFIX} Running: {' '.join(command)}")
    try:
        completed = subprocess.run(command, **_build_run_kwargs())
    except KeyboardInterrupt:
        print(
            f"{PREFIX} Migration run interrupted by a console signal "
            "(for example debugger Ctrl+C propagation)."
        )
        return None

    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)
    return completed


def _has_conflicting_migrations(completed: subprocess.CompletedProcess[str]) -> bool:
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
