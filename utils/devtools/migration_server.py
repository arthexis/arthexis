#!/usr/bin/env python3
"""Run Django migrations for developer launcher workflows."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Protocol

BASE_DIR = Path(__file__).resolve().parents[2]
PREFIX = "[Migration Runner]"
DEFAULT_WATCH_DIRS = ("apps", "config", "utils")
WATCH_FILE_SUFFIXES = (".py", ".pyi")
WATCH_IGNORE_DIRS = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "logs",
    "media",
    "node_modules",
    "staticfiles",
}
CONFLICT_PATTERN = re.compile(
    r"Conflicting migrations detected; multiple leaf nodes in the migration graph",
    re.IGNORECASE,
)


class ProcessLike(Protocol):
    """Protocol for subprocess handles used by the developer migration launcher."""

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
    """Build the command used to execute Django migrations.

    Args:
        extra_args: Optional extra CLI arguments forwarded to ``manage.py migrate``.

    Returns:
        The subprocess command to execute.
    """

    command = [sys.executable, "manage.py", "migrate"]
    if extra_args:
        command.extend(extra_args)
    return command


def build_merge_command() -> list[str]:
    """Build the command used to generate Django merge migrations.

    Returns:
        The subprocess command for automatic migration merging.
    """

    return [sys.executable, "manage.py", "makemigrations", "--merge", "--noinput"]


def _build_popen_kwargs() -> dict[str, object]:
    """Build common subprocess arguments for migration-related commands.

    Returns:
        Shared keyword arguments for ``subprocess.Popen``.
    """

    popen_kwargs: dict[str, object] = {
        "cwd": BASE_DIR,
        "env": _build_subprocess_env(),
        "stderr": subprocess.PIPE,
        "stdout": subprocess.PIPE,
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
    """Build subprocess environment with debug mode disabled for migrations.

    Returns:
        Environment variables for migration subprocesses.
    """

    env = dict(os.environ)
    env["DEBUG"] = "0"
    env["DJANGO_DEBUG"] = "0"
    return env


def _collect_process_result(process: ProcessLike) -> CommandResult:
    """Collect process output into a ``CompletedProcess``-compatible object.

    Args:
        process: Running subprocess handle.

    Returns:
        Completed command output.
    """

    stdout, stderr = process.communicate()
    returncode = process.wait()
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr
    )


def _stop_interrupted_process(process: ProcessLike) -> None:
    """Stop an interrupted process, escalating to force-kill when needed.

    Args:
        process: Running subprocess handle.

    Returns:
        None.
    """

    process.terminate()
    try:
        process.wait()
    except KeyboardInterrupt:
        process.kill()
        process.wait()


def _run_command(command: list[str]) -> CommandResult | None:
    """Run a command while preserving useful console output and signal handling.

    Args:
        command: Subprocess command to execute.

    Returns:
        Completed command output, or ``None`` when interrupted.
    """

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
    """Return ``True`` when command output indicates migration graph conflicts.

    Args:
        completed: Completed migration command output.

    Returns:
        Whether the output reported migration conflicts.
    """

    output = f"{completed.stdout}\n{completed.stderr}"
    return bool(CONFLICT_PATTERN.search(output))


def run_migrations(extra_args: list[str] | None = None) -> int:
    """Run ``manage.py migrate`` and return the subprocess exit code.

    Args:
        extra_args: Optional arguments forwarded to ``manage.py migrate``.

    Returns:
        Migration process exit code.
    """

    completed = _run_command(build_migration_command(extra_args))
    if completed is None:
        return 130

    if completed.returncode != 0 and _has_conflicting_migrations(completed):
        print(f"{PREFIX} Conflicting migrations detected. Attempting automatic merge.")
        merge_completed = _run_command(build_merge_command())
        if merge_completed is None:
            return 130
        if merge_completed.returncode != 0:
            print(
                f"{PREFIX} Automatic merge failed with exit code {merge_completed.returncode}."
            )
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


def _iter_watch_files() -> list[Path]:
    """Return a deterministic list of source files that should trigger reruns.

    Returns:
        Sorted file paths watched for changes.
    """

    files: set[Path] = set()
    for dir_name in DEFAULT_WATCH_DIRS:
        root = BASE_DIR / dir_name
        if not root.is_dir():
            continue

        for dirpath, dirs, filenames in os.walk(root, topdown=True):
            dirs[:] = [
                candidate for candidate in dirs if candidate not in WATCH_IGNORE_DIRS
            ]
            for filename in filenames:
                if filename.endswith(WATCH_FILE_SUFFIXES):
                    files.add(Path(dirpath) / filename)
    return sorted(files)


def _capture_watch_state() -> dict[Path, int]:
    """Capture file modification nanosecond timestamps for watchable source files.

    Returns:
        Mapping of watched file paths to modification timestamps.
    """

    state: dict[Path, int] = {}
    for path in _iter_watch_files():
        try:
            state[path] = path.stat().st_mtime_ns
        except OSError:
            continue
    return state


def _detect_changed_files(
    previous: dict[Path, int], current: dict[Path, int]
) -> list[Path]:
    """Return sorted files that changed between two state snapshots.

    Args:
        previous: Previous file timestamp snapshot.
        current: Current file timestamp snapshot.

    Returns:
        Sorted changed file paths.
    """

    all_paths = previous.keys() | current.keys()
    changed_paths = {
        path for path in all_paths if previous.get(path) != current.get(path)
    }
    return sorted(changed_paths)


def _wait_for_source_change_once(
    *,
    baseline: dict[Path, int],
    interval: float,
    debounce: float,
) -> tuple[dict[Path, int], list[Path]]:
    """Block until source changes settle, then return the new state and changes.

    Args:
        baseline: Existing watched file timestamp snapshot.
        interval: Polling interval in seconds.
        debounce: Debounce window in seconds.

    Returns:
        Updated snapshot plus changed file paths.
    """

    while True:
        time.sleep(interval)
        current_state = _capture_watch_state()
        changed_files = _detect_changed_files(baseline, current_state)
        if not changed_files:
            continue

        stable_until = time.monotonic() + debounce
        while time.monotonic() < stable_until:
            time.sleep(interval)
            next_state = _capture_watch_state()
            next_changes = _detect_changed_files(current_state, next_state)
            if next_changes:
                current_state = next_state
                changed_files = _detect_changed_files(baseline, current_state)
                stable_until = time.monotonic() + debounce

        return current_state, changed_files


def run_migration_server(
    *,
    extra_args: list[str] | None = None,
    interval: float = 1.0,
    debounce: float = 1.0,
    watch: bool = False,
) -> int:
    """Run migrations once or keep rerunning when watched source files change.

    Args:
        extra_args: Optional arguments forwarded to ``manage.py migrate``.
        interval: Polling interval in seconds.
        debounce: Debounce window before a rerun.
        watch: Whether to continue watching for file changes.

    Returns:
        Migration process exit code.
    """

    exit_code = run_migrations(extra_args)
    if not watch or exit_code == 130:
        return exit_code

    interval = max(0.1, interval)
    debounce = max(0.1, debounce)
    baseline = _capture_watch_state()
    print(
        f"{PREFIX} Watching source files for migration reruns "
        f"(interval={interval:.1f}s, debounce={debounce:.1f}s). Press Ctrl+C to stop."
    )

    while True:
        try:
            baseline, changed_files = _wait_for_source_change_once(
                baseline=baseline,
                interval=interval,
                debounce=debounce,
            )
        except KeyboardInterrupt:
            print(f"{PREFIX} Migration server interrupted. Exiting.")
            return 130

        preview = ", ".join(
            str(path.relative_to(BASE_DIR)) for path in changed_files[:3]
        )
        if len(changed_files) > 3:
            preview += f", +{len(changed_files) - 3} more"
        print(f"{PREFIX} Source changes detected ({preview}). Re-running migrations.")
        exit_code = run_migrations(extra_args)
        if exit_code == 130:
            return exit_code


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse launcher arguments for migration runs and optional watch mode.

    Args:
        argv: Optional CLI arguments to parse.

    Returns:
        Parsed command-line arguments.
    """

    parser = argparse.ArgumentParser(
        description="Run Django migrations once or in watch mode."
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Polling interval in seconds when --watch/--server is enabled.",
    )
    parser.add_argument(
        "--debounce",
        type=float,
        default=1.0,
        help="Debounce window in seconds before rerunning migrations in watch mode.",
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
        "--watch",
        action="store_true",
        help="Keep running and re-run migrations when code files change.",
    )
    parser.add_argument(
        "--server",
        action="store_true",
        help="Alias for --watch for legacy migration server launchers.",
    )
    parser.add_argument(
        "extra_args",
        nargs=argparse.REMAINDER,
        help="Additional args passed to `manage.py migrate`.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for developer migration launcher tasks.

    Args:
        argv: Optional CLI arguments.

    Returns:
        Process exit code for the migration workflow.
    """

    args = parse_args(argv)
    extra_args = args.extra_args
    if extra_args and extra_args[0] == "--":
        extra_args = extra_args[1:]
    return run_migration_server(
        extra_args=extra_args,
        interval=args.interval,
        debounce=args.debounce,
        watch=args.watch or args.server,
    )


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
