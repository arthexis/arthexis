#!/usr/bin/env python3
"""Watch source files and run the test suite when changes occur."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from scripts import migration_server

try:  # pragma: no cover - optional dependency in tests
    from core.notifications import notify_async as notify_async  # type: ignore
except Exception:  # pragma: no cover - notifier may be unavailable
    def notify_async(subject: str, body: str = "") -> None:
        """Fallback notification when :mod:`core.notifications` is missing."""

        print(f"Notification: {subject} - {body}")


def build_pytest_command(*, use_last_failed: bool) -> list[str]:
    """Return the pytest command honoring ``use_last_failed``."""

    command = [sys.executable, "-m", "pytest"]
    if use_last_failed:
        command.append("--last-failed")
    return command


def _notify_result(success: bool) -> None:
    """Send an asynchronous notification reflecting the latest test run."""

    body = "Pytest passed." if success else "Pytest failed. Check VS Code output."
    notify_async("Test server run completed", body)


def run_tests(base_dir: Path, *, use_last_failed: bool) -> bool:
    """Execute pytest in *base_dir* and return ``True`` on success."""

    command = build_pytest_command(use_last_failed=use_last_failed)
    env = os.environ.copy()
    env.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    print("[Test Server] Running:", " ".join(command))
    result = subprocess.run(command, cwd=base_dir, env=env)
    success = result.returncode == 0
    if success:
        print("[Test Server] Tests passed.")
    else:
        print("[Test Server] Tests failed.")
    _notify_result(success)
    return success


def run_migrations(base_dir: Path, *, latest: bool = True) -> bool:
    """Execute migrations via ``env-refresh`` and return ``True`` on success."""

    print("[Test Server] Running migrations...")
    success = migration_server.run_env_refresh(base_dir, latest=latest)
    if success:
        print("[Test Server] Migrations completed successfully.")
    else:
        print("[Test Server] Migrations failed.")
    return success


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
    parser.add_argument(
        "--migrations",
        dest="migrations",
        action="store_true",
        default=True,
        help="Run migrations before the test suite (default).",
    )
    parser.add_argument(
        "--no-migrations",
        dest="migrations",
        action="store_false",
        help="Skip migrations before running tests.",
    )
    parser.add_argument(
        "--latest",
        dest="latest",
        action="store_true",
        default=True,
        help="Pass --latest when running migrations (default).",
    )
    parser.add_argument(
        "--no-latest",
        dest="latest",
        action="store_false",
        help="Do not force --latest when running migrations.",
    )
    args = parser.parse_args(argv)

    print("[Test Server] Starting in", BASE_DIR)
    snapshot = migration_server.collect_source_mtimes(BASE_DIR)
    print("[Test Server] Watching for changes... Press Ctrl+C to stop.")
    migrations_ok = not args.migrations or run_migrations(BASE_DIR, latest=args.latest)
    last_run_success = False
    if migrations_ok:
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
            if args.migrations and not run_migrations(BASE_DIR, latest=args.latest):
                last_run_success = False
                snapshot = migration_server.collect_source_mtimes(BASE_DIR)
                continue
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
