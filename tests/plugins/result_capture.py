"""Capture pytest test outcomes and persist them to the application DB."""

from __future__ import annotations

import os
import sys
from collections import defaultdict
from contextlib import nullcontext
from typing import Any

import pytest
from django.db.utils import DatabaseError, OperationalError


def _get_optional_pytest_django_fixture(
    request: pytest.FixtureRequest, fixture_name: str
) -> Any | None:
    """Return a pytest-django fixture when available, otherwise ``None``."""

    try:
        return request.getfixturevalue(fixture_name)
    except pytest.FixtureLookupError:
        return None

COLLECTED_RESULTS: dict[str, dict[str, Any]] = defaultdict(lambda: {"logs": []})
DB_BLOCKER: Any = None


def append_log(report: pytest.TestReport, entry: dict[str, Any]) -> None:
    """Attach captured output and traceback details to a test result payload."""

    log_parts: list[str] = []
    if getattr(report, "capstdout", ""):
        log_parts.append(f"Captured stdout:\n{report.capstdout}")
    if getattr(report, "capstderr", ""):
        log_parts.append(f"Captured stderr:\n{report.capstderr}")
    long_repr = getattr(report, "longreprtext", "") or ""
    if long_repr and (report.failed or report.skipped):
        reason_label = "Skip details" if report.skipped else "Failure details"
        log_parts.append(f"{reason_label}:\n{long_repr}")
    if log_parts:
        entry["logs"].append("\n\n".join(log_parts))


def record_outcome(report: pytest.TestReport, entry: dict[str, Any]) -> None:
    """Store final status and duration for the active report phase."""

    outcome = report.outcome
    if report.failed and report.when != "call":
        outcome = "error"
    if report.when == "call" or report.skipped or report.failed:
        entry["status"] = outcome
        entry["duration"] = report.duration


def store_result(report: pytest.TestReport, item: pytest.Item) -> None:
    """Persist in-memory result metadata for a single test report update."""

    node_id = report.nodeid
    entry = COLLECTED_RESULTS[node_id]
    entry.setdefault("name", getattr(item, "name", node_id.split("::")[-1]))
    record_outcome(report, entry)
    append_log(report, entry)


@pytest.fixture(scope="session", autouse=True)
def capture_db_blocker(request: pytest.FixtureRequest) -> None:
    """Capture ``django_db_blocker`` for use during session-finish result writes."""

    global DB_BLOCKER
    DB_BLOCKER = _get_optional_pytest_django_fixture(request, "django_db_blocker")


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo) -> Any:
    """Hook into test reporting and aggregate per-item result payloads."""

    del call
    outcome = yield
    report: pytest.TestReport = outcome.get_result()
    store_result(report, item)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Flush in-memory test result records to persistent storage at session end."""

    del exitstatus
    try:
        if not COLLECTED_RESULTS:
            return

        use_permanent_db = os.environ.get("ARTHEXIS_TEST_RESULTS_PERMANENT_DB", "0") == "1"
        from apps.tests.domain import RecordedTestResult, persist_results

        results = [
            RecordedTestResult(
                node_id=node_id,
                name=payload.get("name", node_id),
                status=payload.get("status", "error"),
                duration=payload.get("duration"),
                log="\n\n".join(payload.get("logs", [])).strip(),
            )
            for node_id, payload in COLLECTED_RESULTS.items()
        ]

        try:
            db_context = DB_BLOCKER.unblock() if DB_BLOCKER else nullcontext()
            with db_context:
                persist_results(results, use_permanent_db=use_permanent_db)
        except OperationalError as exc:  # pragma: no cover - best effort logging
            reporter = session.config.pluginmanager.get_plugin("terminalreporter")
            message = str(exc)
            if "no such table" in message and "tests_testresult" in message:
                warning = (
                    "Skipping test result persistence because the tests_testresult "
                    "table is unavailable in the active database."
                )
            else:
                warning = f"Unable to persist test results to primary database: {exc}"
            if reporter:
                reporter.write_line(warning, yellow=True)
            else:
                print(warning, file=sys.stderr)
        except RuntimeError as exc:  # pragma: no cover - best effort logging
            reporter = session.config.pluginmanager.get_plugin("terminalreporter")
            message = str(exc)
            if "Database access not allowed" in message:
                warning = (
                    "Skipping test result persistence because database access is "
                    "blocked during pytest session finalization."
                )
            else:
                warning = f"Unable to persist test results to primary database: {exc}"
            if reporter:
                reporter.write_line(warning, yellow=True)
            else:
                print(warning, file=sys.stderr)
        except DatabaseError as exc:  # pragma: no cover - best effort logging
            reporter = session.config.pluginmanager.get_plugin("terminalreporter")
            message = f"Unable to persist test results to primary database: {exc}"
            if reporter:
                reporter.write_line(message, yellow=True)
            else:
                print(message, file=sys.stderr)
    finally:
        from django.db import connections

        connections.close_all()
