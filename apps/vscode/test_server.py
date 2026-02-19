#!/usr/bin/env python3
"""Run env-refresh on changes and execute the full test suite."""

from __future__ import annotations

import argparse
import itertools
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
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
SCREENSHOT_PORT = 8888


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




def test_run_tests_triggers_screenshot_after_unmarked_group(monkeypatch):
    """Ensure screenshot capture runs after successful regular tests."""

    calls: list[str] = []

    def fake_group(_base_dir: Path, *, label: str, marker: str) -> tuple[bool, int | None]:
        del marker
        calls.append(label)
        return True, 0

    screenshot_calls = {"count": 0}

    def fake_capture(_base_dir: Path) -> bool:
        screenshot_calls["count"] += 1
        return True

    monkeypatch.setattr(sys.modules[__name__], "_run_test_group", fake_group)
    monkeypatch.setattr(sys.modules[__name__], "_capture_ci_style_screenshots", fake_capture)

    assert run_tests(Path(".")) is True
    assert calls == ["critical", "unmarked", "integration", "slow"]
    assert screenshot_calls["count"] == 1


def test_run_tests_skips_screenshot_when_regular_group_fails(monkeypatch):
    """Ensure screenshot capture is skipped when regular tests fail."""

    def fake_group(_base_dir: Path, *, label: str, marker: str) -> tuple[bool, int | None]:
        del marker
        if label == "unmarked":
            return False, 1
        return True, 0

    screenshot_calls = {"count": 0}

    def fake_capture(_base_dir: Path) -> bool:
        screenshot_calls["count"] += 1
        return True

    monkeypatch.setattr(sys.modules[__name__], "_run_test_group", fake_group)
    monkeypatch.setattr(sys.modules[__name__], "_capture_ci_style_screenshots", fake_capture)

    assert run_tests(Path(".")) is False
    assert screenshot_calls["count"] == 0

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
        "--color=yes",
        f"--durations={PYTEST_DURATIONS_COUNT}",
        f"--durations-min={PYTEST_DURATIONS_MIN_SECONDS}",
    ]
    env = os.environ.copy()
    env.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    env.setdefault("ARTHEXIS_TEST_RESULTS_PERMANENT_DB", "1")
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
    return sum(int(count) for count, _ in SUMMARY_COUNT_RE.findall(summary_text))


def _strip_ansi(text: str) -> str:
    return re.sub("\x1b\\[[0-9;]*m", "", text)


def run_tests(base_dir: Path) -> bool:
    """Execute the test suite grouped by markers."""

    groups = [
        (label, _build_marker_expression(include=include, exclude=exclude))
        for label, include, exclude in _marker_segments()
    ]
    results: list[tuple[str, bool, int | None]] = []
    for label, marker in groups:
        success, failed_count = _run_test_group(
            base_dir,
            label=label,
            marker=marker,
        )
        results.append((label, success, failed_count))
        if label == "unmarked" and success:
            _capture_ci_style_screenshots(base_dir)

    overall_success = all(result[1] for result in results)
    _report_test_failures(results)
    return overall_success


def _marker_segments() -> list[tuple[str, str | None, tuple[str, ...]]]:
    """Return ordered marker segments for deterministic, non-overlapping test runs."""

    return [
        ("critical", "critical", ()),
        ("unmarked", None, ("critical", "integration", "slow")),
        ("integration", "integration", ("critical",)),
        ("slow", "slow", ("critical", "integration")),
    ]


def _build_marker_expression(*, include: str | None, exclude: tuple[str, ...]) -> str:
    """Build a pytest marker expression from a required marker and excluded markers."""

    exclusions = " and ".join(f"not {marker}" for marker in exclude)
    if include is None:
        if not exclusions:
            raise ValueError(
                "_build_marker_expression requires include or exclude; empty expression is invalid"
            )
        return exclusions
    if not exclusions:
        return include
    return f"{include} and {exclusions}"


def test_marker_segments_are_mutually_exclusive_regression():
    """Regression: ensure every test belongs to exactly one marker segment."""

    marker_segments = _marker_segments()
    all_markers: set[str] = set()
    for _, include, exclude in marker_segments:
        if include:
            all_markers.add(include)
        all_markers.update(exclude)
    tracked_markers = tuple(sorted(all_markers))
    for presence in itertools.product((False, True), repeat=len(tracked_markers)):
        active_markers = {
            marker
            for marker, is_present in zip(tracked_markers, presence, strict=True)
            if is_present
        }
        matches = 0
        for _, include, exclude in marker_segments:
            if include is not None and include not in active_markers:
                continue
            if any(marker in active_markers for marker in exclude):
                continue
            matches += 1
        assert matches == 1
def _capture_ci_style_screenshots(base_dir: Path) -> bool:
    """Capture public and admin screenshots using the CI-style Playwright flow."""

    if not _playwright_is_available(base_dir):
        print(f"{PREFIX} Skipping screenshot capture: Playwright is unavailable.")
        return False

    artifacts_dir = base_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    admin_password = os.environ.get("DOCS_ADMIN_PASSWORD", "admin")

    manage_env = os.environ.copy()
    manage_env.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    create_admin = [
        sys.executable,
        str(base_dir / "manage.py"),
        "create_docs_admin",
        "--confirm",
        "--password",
        admin_password,
    ]
    print(f"{PREFIX} Ensuring docs admin user exists before screenshots.")
    if subprocess.run(create_admin, cwd=base_dir, env=manage_env).returncode != 0:
        print(f"{PREFIX} Skipping screenshot capture: unable to create docs admin user.")
        return False

    runserver_command = [
        sys.executable,
        str(base_dir / "manage.py"),
        "runserver",
        f"0.0.0.0:{SCREENSHOT_PORT}",
        "--noreload",
    ]
    server_env = manage_env.copy()
    server_env.setdefault("DJANGO_SUPPRESS_MIGRATION_CHECK", "1")
    server_log = artifacts_dir / "test-server-screenshot-server.log"
    with server_log.open("w", encoding="utf-8") as handle:
        server_process = subprocess.Popen(
            runserver_command,
            cwd=base_dir,
            env=server_env,
            stdout=handle,
            stderr=subprocess.STDOUT,
            text=True,
        )
    try:
        if not _wait_for_server(f"http://localhost:{SCREENSHOT_PORT}/admin/login/?next=/admin/"):
            print(
                f"{PREFIX} Skipping screenshot capture: application server did not become healthy."
            )
            return False
        return _run_screenshot_capture_script(base_dir)
    finally:
        server_process.terminate()
        try:
            server_process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server_process.kill()
            server_process.wait(timeout=5)


def _playwright_is_available(base_dir: Path) -> bool:
    """Return ``True`` when Node.js and Playwright are available."""

    if not shutil.which("node"):
        return False
    result = subprocess.run(
        ["node", "-e", "require('playwright')"],
        cwd=base_dir,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def _wait_for_server(url: str, *, attempts: int = 30, delay_seconds: float = 1.0) -> bool:
    """Poll ``url`` until the server responds with a successful status."""

    for _ in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=5):  # noqa: S310
                return True
        except (urllib.error.URLError, TimeoutError):
            time.sleep(delay_seconds)
    return False


def _run_screenshot_capture_script(base_dir: Path) -> bool:
    """Run a temporary Node.js script that captures admin and public screenshots."""

    script_content = """
const { chromium } = require('playwright');

const username = process.env.ADMIN_USERNAME || 'admin';
const password = process.env.ADMIN_PASSWORD || 'admin';
const adminUrl = process.env.ADMIN_URL || 'http://localhost:8888/admin/login/?next=/admin/';
const publicUrl = process.env.PUBLIC_URL || 'http://localhost:8888/';

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();

  await page.goto(adminUrl, { waitUntil: 'domcontentloaded' });
  await page.fill('input[name="username"]', username);
  await page.fill('input[name="password"]', password);
  await Promise.all([
    page.waitForNavigation({ waitUntil: 'domcontentloaded' }),
    page.click('input[type="submit"], button[type="submit"]'),
  ]);
  await page.screenshot({ path: 'artifacts/admin-dashboard.png', fullPage: true });

  await page.goto(publicUrl, { waitUntil: 'domcontentloaded' });
  await page.screenshot({ path: 'artifacts/public-site.png', fullPage: true });

  await browser.close();
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
""".strip()
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".js",
        encoding="utf-8",
        dir=base_dir,
        delete=False,
    ) as script_file:
        script_file.write(script_content)
        script_path = Path(script_file.name)

    env = os.environ.copy()
    env.setdefault("ADMIN_USERNAME", "admin")
    env.setdefault("ADMIN_PASSWORD", os.environ.get("DOCS_ADMIN_PASSWORD", "admin"))
    env.setdefault(
        "ADMIN_URL", f"http://localhost:{SCREENSHOT_PORT}/admin/login/?next=/admin/"
    )
    env.setdefault("PUBLIC_URL", f"http://localhost:{SCREENSHOT_PORT}/")

    try:
        result = subprocess.run(["node", str(script_path)], cwd=base_dir, env=env)
        if result.returncode != 0:
            print(f"{PREFIX} Screenshot capture failed. See output above for details.")
            return False
        print(
            f"{PREFIX} Screenshot capture completed: artifacts/admin-dashboard.png, artifacts/public-site.png"
        )
        return True
    finally:
        try:
            script_path.unlink()
        except OSError:
            pass


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
