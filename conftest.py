from __future__ import annotations

import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Set

import django
import pytest

# Force lightweight SQLite settings during tests to avoid slow Postgres
# connection attempts when checking availability inside config.settings.
os.environ.setdefault("ARTHEXIS_DB_BACKEND", "sqlite")
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

from django.conf import settings  # noqa: E402


class _DisableMigrations(dict):
    """Short-circuit Django migrations for faster test database setup."""

    def __contains__(self, item: object) -> bool:  # pragma: no cover - trivial
        return True

    def __getitem__(self, item: str) -> None:  # pragma: no cover - trivial
        return None


if os.environ.get("PYTEST_DISABLE_MIGRATIONS", "0") == "1":
    settings.MIGRATION_MODULES = _DisableMigrations()

from apps.tests.domain import RecordedTestResult, persist_results  # noqa: E402
COLLECTED_RESULTS: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"logs": []})
DB_BLOCKER = None


def _normalize_marker_value(value: str) -> str:
    return value.strip().lower()


def _parse_csv_env(value: str | None) -> Set[str] | None:
    if not value:
        return None
    return {_normalize_marker_value(item) for item in value.split(",") if item.strip()}


def _truthy_env(value: str | None) -> bool:
    if not value:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _should_skip_for_role(item: pytest.Item, role_filter: str | None, role_only: bool) -> str | None:
    role_marks = [
        _normalize_marker_value(mark.args[0])
        for mark in item.iter_markers("role")
        if mark.args and isinstance(mark.args[0], str)
    ]
    if role_filter:
        normalized_role = _normalize_marker_value(role_filter)
        if role_marks and normalized_role not in role_marks:
            return f"role '{role_filter}' not in {sorted(set(role_marks))}"
    if role_only and not role_marks:
        return "role-specific run requested"
    return None


def _should_skip_for_features(item: pytest.Item, enabled_features: Set[str] | None) -> str | None:
    if enabled_features is None:
        return None
    feature_marks = [
        _normalize_marker_value(mark.args[0])
        for mark in item.iter_markers("feature")
        if mark.args and isinstance(mark.args[0], str)
    ]
    if feature_marks and not set(feature_marks).issubset(enabled_features):
        return f"features {sorted(set(feature_marks))} not in {sorted(enabled_features)}"
    return None


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    role_filter = os.environ.get("NODE_ROLE")
    role_only = _truthy_env(os.environ.get("NODE_ROLE_ONLY"))
    enabled_features = _parse_csv_env(os.environ.get("NODE_FEATURES"))

    for item in items:
        role_skip_reason = _should_skip_for_role(item, role_filter, role_only)
        if role_skip_reason:
            item.add_marker(pytest.mark.skip(reason=role_skip_reason))
            continue

        feature_skip_reason = _should_skip_for_features(item, enabled_features)
        if feature_skip_reason:
            item.add_marker(pytest.mark.skip(reason=feature_skip_reason))

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
