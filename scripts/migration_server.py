#!/usr/bin/env python3
"""Watch source files and run ``env-refresh`` when changes occur."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, Iterable

BASE_DIR = Path(__file__).resolve().parents[1]

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

try:  # pragma: no cover - optional dependency in tests
    from core.notifications import notify_async as notify_async  # type: ignore
except Exception:  # pragma: no cover - the notifier is optional
    def notify_async(subject: str, body: str = "") -> None:
        """Fallback notification when :mod:`core.notifications` is unavailable."""

        print(f"Notification: {subject} - {body}")


WATCH_EXTENSIONS = {
    ".py",
    ".pyi",
    ".html",
    ".htm",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".css",
    ".scss",
    ".sass",
    ".json",
    ".yaml",
    ".yml",
    ".ini",
    ".cfg",
    ".toml",
    ".po",
    ".mo",
    ".txt",
    ".sh",
    ".bat",
}

WATCH_FILENAMES = {
    "Dockerfile",
    "Makefile",
    "manage.py",
    "pyproject.toml",
    "requirements.txt",
    "env-refresh.py",
}

EXCLUDED_DIR_NAMES = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".vscode",
    ".idea",
    "__pycache__",
    "backups",
    "build",
    "dist",
    "docs",
    "htmlcov",
    "logs",
    "node_modules",
    "releases",
    "static",
    "tmp",
    ".venv",
}


def _should_skip_dir(parts: Iterable[str]) -> bool:
    """Return ``True`` when any component in *parts* should be ignored."""

    for part in parts:
        if part in EXCLUDED_DIR_NAMES:
            return True
        if part.startswith(".") and part not in WATCH_FILENAMES:
            return True
    return False


def _should_watch_file(relative_path: Path) -> bool:
    """Return ``True`` when *relative_path* represents a watched file."""

    if relative_path.name in WATCH_FILENAMES:
        return True
    return relative_path.suffix.lower() in WATCH_EXTENSIONS


def collect_source_mtimes(base_dir: Path) -> Dict[str, int]:
    """Return a snapshot of watched files under *base_dir*."""

    snapshot: Dict[str, int] = {}
    for root, dirs, files in os.walk(base_dir):
        rel_root = Path(root).relative_to(base_dir)
        if _should_skip_dir(rel_root.parts):
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if not _should_skip_dir((*rel_root.parts, d))]
        for name in files:
            rel_path = rel_root / name
            if not _should_watch_file(rel_path):
                continue
            full_path = Path(root, name)
            try:
                snapshot[str(rel_path)] = full_path.stat().st_mtime_ns
            except FileNotFoundError:
                continue
    return snapshot


def diff_snapshots(previous: Dict[str, int], current: Dict[str, int]) -> list[str]:
    """Return a human readable summary of differences between two snapshots."""

    changes: list[str] = []
    prev_keys = set(previous)
    curr_keys = set(current)
    for added in sorted(curr_keys - prev_keys):
        changes.append(f"added {added}")
    for removed in sorted(prev_keys - curr_keys):
        changes.append(f"removed {removed}")
    for common in sorted(prev_keys & curr_keys):
        if previous[common] != current[common]:
            changes.append(f"modified {common}")
    return changes


def build_env_refresh_command(base_dir: Path, *, latest: bool = True) -> list[str]:
    """Return the command used to run ``env-refresh`` from *base_dir*."""

    script = base_dir / "env-refresh.py"
    if not script.exists():
        raise FileNotFoundError("env-refresh.py not found")
    command = [sys.executable, str(script)]
    if latest:
        command.append("--latest")
    command.append("database")
    return command


def run_env_refresh(base_dir: Path, *, latest: bool = True) -> bool:
    """Run env-refresh and return ``True`` when the command succeeds."""

    command = build_env_refresh_command(base_dir, latest=latest)
    env = os.environ.copy()
    env.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    print("[Migration Server] Running:", " ".join(command))
    result = subprocess.run(command, cwd=base_dir, env=env)
    if result.returncode != 0:
        notify_async(
            "Migration failure",
            "Check VS Code output for env-refresh details.",
        )
        return False
    return True


def wait_for_changes(base_dir: Path, snapshot: Dict[str, int], *, interval: float) -> Dict[str, int]:
    """Block until watched files differ from *snapshot* and return the update."""

    while True:
        time.sleep(max(0.1, interval))
        current = collect_source_mtimes(base_dir)
        if current != snapshot:
            return current


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run env-refresh whenever source code changes are detected."
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Polling interval (seconds) before checking for updates.",
    )
    parser.add_argument(
        "--latest",
        dest="latest",
        action="store_true",
        default=True,
        help="Pass --latest to env-refresh (default).",
    )
    parser.add_argument(
        "--no-latest",
        dest="latest",
        action="store_false",
        help="Do not force --latest when invoking env-refresh.",
    )
    parser.add_argument(
        "--debounce",
        type=float,
        default=1.0,
        help="Sleep for this many seconds after detecting a change to allow batches.",
    )
    args = parser.parse_args(argv)

    print("[Migration Server] Starting in", BASE_DIR)
    snapshot = collect_source_mtimes(BASE_DIR)
    print("[Migration Server] Watching for changes... Press Ctrl+C to stop.")

    try:
        while True:
            updated = wait_for_changes(BASE_DIR, snapshot, interval=args.interval)
            if args.debounce > 0:
                time.sleep(args.debounce)
                updated = collect_source_mtimes(BASE_DIR)
                if updated == snapshot:
                    continue
            change_summary = diff_snapshots(snapshot, updated)
            if change_summary:
                display = "; ".join(change_summary[:5])
                if len(change_summary) > 5:
                    display += "; ..."
                print(f"[Migration Server] Changes detected: {display}")
            success = run_env_refresh(BASE_DIR, latest=args.latest)
            if success:
                print("[Migration Server] env-refresh completed successfully.")
            else:
                print("[Migration Server] env-refresh failed. Awaiting further changes.")
            snapshot = collect_source_mtimes(BASE_DIR)
    except KeyboardInterrupt:
        print("[Migration Server] Stopped.")
        return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
