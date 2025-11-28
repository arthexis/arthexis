#!/usr/bin/env python3
"""Watch source files and run the test suite when changes occur."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

from scripts import migration_server

BASE_DIR = Path(__file__).resolve().parents[1]


def build_pytest_command(*, use_last_failed: bool) -> list[str]:
    """Return the pytest command honoring ``use_last_failed``."""

    command = [sys.executable, "-m", "pytest"]
    if use_last_failed:
        command.append("--last-failed")
    return command


def run_tests(base_dir: Path, *, use_last_failed: bool) -> bool:
    """Execute pytest in *base_dir* and return ``True`` on success."""

    command = build_pytest_command(use_last_failed=use_last_failed)
    env = os.environ.copy()
    env.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    print("[Test Server] Running:", " ".join(command))
    result = subprocess.run(command, cwd=base_dir, env=env)
    if result.returncode == 0:
        print("[Test Server] Tests passed.")
        return True
    print("[Test Server] Tests failed.")
    return False


def _summarize_changes(previous: dict[str, int], current: dict[str, int]) -> None:
    change_summary = migration_server.diff_snapshots(previous, current)
    if change_summary:
        display = "; ".join(change_summary[:5])
        if len(change_summary) > 5:
            display += "; ..."
        print(f"[Test Server] Changes detected: {display}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run pytest whenever source code changes are detected.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Polling interval (seconds) before checking for updates.",
    )
    parser.add_argument(
        "--debounce",
        type=float,
        default=1.0,
        help="Sleep for this many seconds after detecting a change to allow batches.",
    )
    parser.add_argument(
        "--last-failed",
        dest="last_failed",
        action="store_true",
        default=True,
        help="Use --last-failed after a failure (default).",
    )
    parser.add_argument(
        "--no-last-failed",
        dest="last_failed",
        action="store_false",
        help="Always run the full test suite.",
    )
    args = parser.parse_args(argv)

    print("[Test Server] Starting in", BASE_DIR)
    snapshot = migration_server.collect_source_mtimes(BASE_DIR)
    print("[Test Server] Watching for changes... Press Ctrl+C to stop.")
    last_run_success = run_tests(BASE_DIR, use_last_failed=False)

    try:
        while True:
            updated = migration_server.wait_for_changes(
                BASE_DIR, snapshot, interval=args.interval
            )
            if args.debounce > 0:
                time.sleep(args.debounce)
                updated = migration_server.collect_source_mtimes(BASE_DIR)
                if updated == snapshot:
                    continue
            _summarize_changes(snapshot, updated)
            last_run_success = run_tests(
                BASE_DIR,
                use_last_failed=args.last_failed and not last_run_success,
            )
            snapshot = migration_server.collect_source_mtimes(BASE_DIR)
    except KeyboardInterrupt:
        print("[Test Server] Stopped.")
        return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
