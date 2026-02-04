#!/usr/bin/env python3
"""Run env-refresh on changes and execute the full test suite."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, NamedTuple

import pytest

from . import migration_server as migration

BASE_DIR = migration.BASE_DIR
LOCK_DIR = migration.LOCK_DIR
NOTIFY = migration.notify_async

collect_source_mtimes = migration.collect_source_mtimes
diff_snapshots = migration.diff_snapshots
wait_for_changes = migration.wait_for_changes

PREFIX = "[Test Server]"
PYTEST_DURATIONS_COUNT = 5
PYTEST_DURATIONS_MIN_SECONDS = 0.0
SUMMARY_LINE_RE = re.compile(r"=+ (.+?) =+")
SUMMARY_COUNT_RE = re.compile(r"(\d+)\s+(failed|error|errors)")


@pytest.fixture
def lock_dir(tmp_path: Path) -> Path:
    """Provide an isolated lock directory for tests."""

    return tmp_path / "locks"


@contextmanager
def server_state(lock_dir: Path):
    """Context manager that records the test server PID."""

    lock_dir.mkdir(parents=True, exist_ok=True)
    state_path = lock_dir / "test_server.json"

    payload = {"pid": os.getpid(), "timestamp": time.time()}
    try:
        state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError:
        pass
    try:
        yield state_path
    finally:
        try:
            state_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


@pytest.fixture(name="test_server_state")
def test_server_state_fixture(lock_dir: Path):
    """Fixture wrapper to align with existing test usage."""

    with server_state(lock_dir) as state_path:
        yield state_path


def test_server_state_creates_and_cleans(lock_dir: Path):
    """Ensure the server state file is created and then removed."""

    with server_state(lock_dir) as state_path:
        assert state_path.exists()

        payload = json.loads(state_path.read_text(encoding="utf-8"))
        assert payload.get("pid") == os.getpid()
        assert "timestamp" in payload

    assert not state_path.exists()


def update_requirements(base_dir: Path) -> bool:
    """Install Python requirements when the lockfile hash changes."""

    req_file = base_dir / migration.REQUIREMENTS_FILE
    hash_file = base_dir / migration.REQUIREMENTS_HASH_FILE
    helper_script = base_dir / migration.PIP_INSTALL_HELPER

    hash_file.parent.mkdir(parents=True, exist_ok=True)

    if not req_file.exists():
        return False

    try:
        current_hash = migration._hash_file(req_file)
    except OSError:
        return False

    try:
        stored_hash = hash_file.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        stored_hash = ""
    except OSError:
        stored_hash = ""

    if current_hash == stored_hash:
        return False

    print(f"{PREFIX} Installing Python requirements...")
    if helper_script.exists():
        command = [sys.executable, str(helper_script), "-r", str(req_file)]
    else:
        command = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "-r",
            str(req_file),
        ]

    result = subprocess.run(command, cwd=base_dir)
    if result.returncode != 0:
        print(f"{PREFIX} Failed to install Python requirements.")
        NOTIFY(
            "Python requirements update failed",
            "See test server output for details.",
        )
        return False

    try:
        hash_file.write_text(current_hash, encoding="utf-8")
    except OSError:
        pass

    print(f"{PREFIX} Python requirements updated.")
    return True


def run_env_refresh(base_dir: Path, *, latest: bool = True) -> bool:
    """Run env-refresh and return ``True`` when the command succeeds."""

    try:
        command = migration.build_env_refresh_command(base_dir, latest=latest)
    except FileNotFoundError as exc:
        print(f"{PREFIX} {exc}")
        return False

    env = os.environ.copy()
    env.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    print(f"{PREFIX} Running:", " ".join(command))
    result = subprocess.run(command, cwd=base_dir, env=env)
    if result.returncode != 0:
        NOTIFY(
            "Migration failure",
            "Check VS Code output for env-refresh details.",
        )
        return False
    return True


def _migration_merge_required(base_dir: Path) -> bool:
    """Return ``True`` if a merge migration is required."""

    command = [
        sys.executable,
        str(base_dir / "manage.py"),
        "makemigrations",
        "--check",
        "--dry-run",
        "--noinput",
    ]
    env = os.environ.copy()
    env.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    print(f"{PREFIX} Checking for merge migrations:", " ".join(command))
    result = subprocess.run(
        command,
        cwd=base_dir,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    output = result.stdout or ""
    if "Conflicting migrations detected" in output:
        print(f"{PREFIX} Migration merge required. Stopping.")
        NOTIFY(
            "Migration merge required",
            "Resolve conflicting migrations before restarting the test server.",
        )
        return True
    return False


def _run_test_group(
    base_dir: Path, *, label: str, marker: str
) -> tuple[bool, int | None]:
    """Execute a group of pytest tests filtered by markers."""

    command = [
        sys.executable,
        "-m",
        "pytest",
        "-m",
        marker,
        f"--durations={PYTEST_DURATIONS_COUNT}",
        f"--durations-min={PYTEST_DURATIONS_MIN_SECONDS}",
    ]
    env = os.environ.copy()
    env.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    print(f"{PREFIX} Running {label} tests:", " ".join(command))
    started_at = time.monotonic()
    result = _run_command_with_output(command, cwd=base_dir, env=env)
    elapsed = migration._format_elapsed(time.monotonic() - started_at)
    if result.returncode != 0:
        NOTIFY(
            "Test suite failure",
            "Check test server output for pytest details.",
        )
        print(f"{PREFIX} {label} tests failed after {elapsed}.")
        return False, result.failed_count

    print(f"{PREFIX} {label} tests completed successfully in {elapsed}.")
    return True, result.failed_count


class _TestRunResult(NamedTuple):
    returncode: int
    failed_count: int | None


def _run_command_with_output(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
) -> _TestRunResult:
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
    )
    output_lines: list[str] = []
    assert process.stdout is not None
    for line in process.stdout:
        print(line, end="")
        output_lines.append(line)
    returncode = process.wait()
    failed_count = _extract_failed_count(output_lines)
    return _TestRunResult(returncode=returncode, failed_count=failed_count)


def _extract_failed_count(output_lines: Iterable[str]) -> int | None:
    summary_text = None
    for line in output_lines:
        match = SUMMARY_LINE_RE.search(_strip_ansi(line))
        if match:
            summary_text = match.group(1)
    if not summary_text:
        return None
    if " in " in summary_text:
        summary_text = summary_text.split(" in ", 1)[0]
    failed_count = 0
    for count, label in SUMMARY_COUNT_RE.findall(summary_text):
        if label in {"failed", "error", "errors"}:
            failed_count += int(count)
    return failed_count


def _strip_ansi(text: str) -> str:
    return re.sub("\x1b\\[[0-9;]*m", "", text)


def run_tests(base_dir: Path) -> bool:
    """Execute the test suite grouped by markers."""

    groups = [
        ("critical", "critical"),
        ("unmarked", "not critical and not integration and not slow"),
        ("integration", "integration"),
        ("slow", "slow"),
    ]
    results: list[tuple[str, bool, int | None]] = []
    for label, marker in groups:
        success, failed_count = _run_test_group(
            base_dir,
            label=label,
            marker=marker,
        )
        results.append((label, success, failed_count))

    overall_success = all(result[1] for result in results)
    _report_test_failures(results)
    return overall_success


def _report_test_failures(results: Iterable[tuple[str, bool, int | None]]) -> None:
    failures_present = False
    parts = []
    for label, success, failed_count in results:
        if failed_count is None:
            count_display = "unknown"
        else:
            count_display = str(failed_count)
            if failed_count > 0:
                failures_present = True
        if not success:
            failures_present = True
        parts.append(f"{label}: {count_display} failed")
    if failures_present:
        print(f"{PREFIX} WARNING: Test failures summary - {', '.join(parts)}")


def run_env_refresh_with_tests(base_dir: Path, *, latest: bool) -> bool:
    """Run env-refresh and then execute the full test suite."""

    if _migration_merge_required(base_dir):
        return False
    if run_env_refresh(base_dir, latest=latest):
        print(f"{PREFIX} env-refresh completed successfully.")
        migration.request_runserver_restart(LOCK_DIR)
        run_tests(base_dir)
    else:
        print(f"{PREFIX} env-refresh failed. Awaiting further changes.")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run env-refresh and pytest whenever source code changes are detected.",
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

    update_requirements(BASE_DIR)
    print(PREFIX, "Starting in", BASE_DIR)
    snapshot = collect_source_mtimes(BASE_DIR)
    print(PREFIX, "Watching for changes... Press Ctrl+C to stop.")
    with server_state(LOCK_DIR):
        if not run_env_refresh_with_tests(BASE_DIR, latest=args.latest):
            return 0
        snapshot = collect_source_mtimes(BASE_DIR)

        try:
            while True:
                updated = wait_for_changes(BASE_DIR, snapshot, interval=args.interval)
                if args.debounce > 0:
                    time.sleep(args.debounce)
                    updated = collect_source_mtimes(BASE_DIR)
                    if updated == snapshot:
                        continue
                if update_requirements(BASE_DIR):
                    NOTIFY(
                        "New Python requirements installed",
                        "The test server stopped after installing new dependencies.",
                    )
                    print(
                        f"{PREFIX} New Python requirements installed. Stopping."
                    )
                    return 0
                change_summary = diff_snapshots(snapshot, updated)
                if change_summary:
                    display = "; ".join(change_summary[:5])
                    if len(change_summary) > 5:
                        display += "; ..."
                    print(f"{PREFIX} Changes detected: {display}")
                if not run_env_refresh_with_tests(BASE_DIR, latest=args.latest):
                    return 0
                snapshot = collect_source_mtimes(BASE_DIR)
        except KeyboardInterrupt:
            print(f"{PREFIX} Stopped.")
            return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
