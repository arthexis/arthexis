from __future__ import annotations

import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List

import django
import pytest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")


def _ensure_clean_test_databases() -> None:
    base_dir = Path(__file__).resolve().parent
    candidates = [
        base_dir / "test_db.sqlite3",
        base_dir / "work" / "test_db.sqlite3",
        base_dir / "work" / "test_db" / "test_db.sqlite3",
    ]

    for path in candidates:
        if path.exists():
            path.unlink()

    (base_dir / "work" / "test_db").mkdir(parents=True, exist_ok=True)


_ensure_clean_test_databases()
django.setup()

from apps.tests.domain import RecordedTestResult, persist_results  # noqa: E402
COLLECTED_RESULTS: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"logs": []})
DB_BLOCKER = None


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


def _extract_features(item: pytest.Item) -> list[dict[str, str | None]]:
    features: list[dict[str, str | None]] = []
    for mark in item.iter_markers("feature"):
        candidates: list[object] = []
        if mark.kwargs:
            candidates.append(dict(mark.kwargs))
        candidates += list(mark.args)
        for candidate in candidates:
            if isinstance(candidate, dict):
                slug = candidate.get("slug")
                package = candidate.get("package")
            elif isinstance(candidate, str):
                slug = candidate
                package = None
            else:
                continue
            if not slug:
                continue
            feature_payload = {"slug": slug, "package": package}
            if feature_payload not in features:
                features.append(feature_payload)
    return features


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
    if not entry.get("features"):
        entry["features"] = _extract_features(item)
    _record_outcome(report, entry)
    _append_log(report, entry)


@pytest.fixture(scope="session", autouse=True)
def _capture_db_blocker(django_db_blocker: Any) -> None:
    global DB_BLOCKER
    DB_BLOCKER = django_db_blocker


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
            features=payload.get("features", []),
        )
        for node_id, payload in COLLECTED_RESULTS.items()
    ]

    try:
        if DB_BLOCKER:
            with DB_BLOCKER.unblock():
                persist_results(results)
        else:
            persist_results(results)
    except Exception as exc:  # pragma: no cover - best effort logging
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        message = f"Unable to persist test results to primary database: {exc}"
        if reporter:
            reporter.write_line(message, yellow=True)
        else:
            print(message, file=sys.stderr)
