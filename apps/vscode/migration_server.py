#!/usr/bin/env python3
"""Run Django migrations once for VS Code launcher workflows."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
PREFIX = "[Migration Runner]"


def build_migration_command(extra_args: list[str] | None = None) -> list[str]:
    """Build the command used to execute Django migrations."""

    command = [sys.executable, "manage.py", "migrate"]
    if extra_args:
        command.extend(extra_args)
    return command


def run_migrations(extra_args: list[str] | None = None) -> int:
    """Run ``manage.py migrate`` and return the subprocess exit code."""

    command = build_migration_command(extra_args)
    print(f"{PREFIX} Running: {' '.join(command)}")
    completed = subprocess.run(command, cwd=BASE_DIR, check=False)
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
