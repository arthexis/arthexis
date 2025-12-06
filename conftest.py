from __future__ import annotations

import copy
import os
import sys
from collections import defaultdict
from typing import Any, Dict, List

import django
import pytest
from django.conf import settings

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from apps.tests.domain import RecordedTestResult, persist_results  # noqa: E402

PRIMARY_DATABASE_SETTINGS: Dict[str, Any] = copy.deepcopy(settings.DATABASES["default"])
COLLECTED_RESULTS: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"logs": []})


def _append_log(report: pytest.TestReport, entry: Dict[str, Any]) -> None:
    log_parts: List[str] = []
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


def _record_outcome(report: pytest.TestReport, entry: Dict[str, Any]) -> None:
    outcome = report.outcome
    if report.failed and report.when != "call":
        outcome = "error"
    if report.when == "call" or report.skipped or report.failed:
        entry["status"] = outcome
        entry["duration"] = report.duration


def _store_result(report: pytest.TestReport, item: pytest.Item) -> None:
    node_id = report.nodeid
    entry = COLLECTED_RESULTS[node_id]
    entry.setdefault("name", getattr(item, "name", node_id.split("::")[-1]))
    _record_outcome(report, entry)
    _append_log(report, entry)


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo) -> Any:
    outcome = yield
    report: pytest.TestReport = outcome.get_result()
    _store_result(report, item)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if not COLLECTED_RESULTS:
        return

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
        persist_results(results, PRIMARY_DATABASE_SETTINGS)
    except Exception as exc:  # pragma: no cover - best effort logging
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        message = f"Unable to persist test results to primary database: {exc}"
        if reporter:
            reporter.write_line(message, yellow=True)
        else:
            print(message, file=sys.stderr)
