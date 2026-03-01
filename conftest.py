from __future__ import annotations

import atexit
import os
import sys
import tempfile
from contextlib import nullcontext
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List

import django
import pytest


_PYTEST_SQLITE_TMP_DIR: tempfile.TemporaryDirectory[str] | None = None

# Force lightweight SQLite settings during tests to avoid slow Postgres
# connection attempts when checking availability inside config.settings.
os.environ.setdefault("ARTHEXIS_DB_BACKEND", "sqlite")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")


def _sqlite_path_is_writable(path_value: str) -> bool:
    """Return True when the SQLite path parent directory accepts writes."""

    candidate = Path(path_value).expanduser()
    parent = candidate.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=parent):
            pass
    except OSError:
        return False
    return True


def _sqlite_uses_special_name(path_value: str) -> bool:
    """Return True for SQLite values that are not filesystem paths."""

    value = path_value.strip()
    return value == ":memory:" or value.startswith("file:")


def _set_writable_sqlite_env(var_name: str, fallback: Path) -> None:
    """Set SQLite env vars to writable paths while preserving valid caller overrides."""

    configured = os.environ.get(var_name)
    if configured and (_sqlite_uses_special_name(configured) or _sqlite_path_is_writable(configured)):
        return
    os.environ[var_name] = str(fallback)


def _configure_ephemeral_sqlite_paths() -> None:
    """Route SQLite DBs to writable temporary paths for stable pytest setup."""

    global _PYTEST_SQLITE_TMP_DIR
    _PYTEST_SQLITE_TMP_DIR = tempfile.TemporaryDirectory(prefix=f"arthexis-pytest-{os.getpid()}-")
    atexit.register(_PYTEST_SQLITE_TMP_DIR.cleanup)
    db_root = Path(_PYTEST_SQLITE_TMP_DIR.name)
    _set_writable_sqlite_env("ARTHEXIS_SQLITE_PATH", db_root / "default.sqlite3")
    _set_writable_sqlite_env("ARTHEXIS_SQLITE_TEST_PATH", db_root / "test.sqlite3")


_configure_ephemeral_sqlite_paths()


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

REQUIRES_DB = False
COLLECTED_RESULTS: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"logs": []})
DB_BLOCKER = None


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register suite-level options for PR-scoped dynamic test selection."""

    parser.addoption(
        "--current-pr",
        action="store",
        default=None,
        metavar="PR",
        help="PR reference used to include tests marked for the current change set.",
    )


def _normalize_pr_reference(value: object) -> str | None:
    """Normalize marker and CLI PR references into comparable uppercase strings."""

    if not isinstance(value, str):
        return None
    normalized = value.strip().upper()
    return normalized or None


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


def _requires_db(item: pytest.Item) -> bool:
    if item.get_closest_marker("django_db") is not None:
        return True
    if {"db", "transactional_db"}.intersection(item.fixturenames):
        return True
    test_class = getattr(item, "cls", None)
    if test_class is None:
        return False
    from django.test import TransactionTestCase

    return issubclass(test_class, TransactionTestCase)


def pytest_configure(config: pytest.Config) -> None:
    markexpr = getattr(config.option, "markexpr", "")
    if markexpr and "critical" in markexpr:
        expanded = markexpr
        if "regression" not in expanded:
            expanded = f"({expanded}) or regression"
        if "noncritical_regression" not in expanded:
            expanded = f"({expanded}) and not noncritical_regression"
        config.option.markexpr = expanded
    config.addinivalue_line(
        "markers",
        "pr_current: dynamically applied to tests whose pytest.mark.pr reference matches --current-pr",
    )


def pytest_collection_modifyitems(session: pytest.Session, config: pytest.Config, items: list[pytest.Item]) -> None:
    """Apply global collection-time marker behavior used across the test suite."""
    global REQUIRES_DB
    REQUIRES_DB = any(_requires_db(item) for item in items)
    is_windows = os.name == "nt"
    windows_nmcli_skip = pytest.mark.skip(reason="nmcli setup script tests are not supported on Windows environments")
    selected_pr = _normalize_pr_reference(config.getoption("--current-pr"))
    should_extend_markexpr = bool(selected_pr)
    for item in items:
        if (
            item.get_closest_marker("regression")
            and not item.get_closest_marker("critical")
            and not item.get_closest_marker("noncritical_regression")
        ):
            item.add_marker("critical")

        if selected_pr:
            for marker in item.iter_markers(name="pr"):
                marker_reference = _normalize_pr_reference(marker.args[0] if marker.args else None)
                if marker_reference == selected_pr:
                    item.add_marker("pr_current")
                    break

        if is_windows and item.nodeid.startswith("scripts/tests/test_nmcli_setup_script.py"):
            item.add_marker(windows_nmcli_skip)

    if should_extend_markexpr:
        markexpr = (config.option.markexpr or "").strip()
        if not markexpr:
            config.option.markexpr = "pr_current"
        elif "pr_current" not in markexpr:
            config.option.markexpr = f"({markexpr}) or pr_current"


@pytest.fixture(scope="session", autouse=True)
def _setup_db_for_django_tests(request: pytest.FixtureRequest, django_db_blocker: Any) -> None:
    """Initialize the Django test database once for DB-backed tests."""
    if not REQUIRES_DB:
        return
    request.getfixturevalue("django_db_setup")


@pytest.fixture(scope="session")
def _load_sigil_roots_once(django_db_setup: Any, django_db_blocker: Any) -> None:
    """Load SigilRoot fixtures once per session for tests that need the DB."""
    from apps.sigils.loader import load_fixture_sigil_roots

    with django_db_blocker.unblock():
        load_fixture_sigil_roots(using="default")


@pytest.fixture(autouse=True)
def _ensure_fixture_sigil_roots(request: pytest.FixtureRequest) -> None:
    if _requires_db(request.node) and request.node.get_closest_marker("sigil_roots"):
        request.getfixturevalue("_load_sigil_roots_once")


@pytest.fixture
def sigil_roots(request: pytest.FixtureRequest) -> None:
    request.getfixturevalue("_load_sigil_roots_once")


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo) -> Any:
    outcome = yield
    report: pytest.TestReport = outcome.get_result()
    _store_result(report, item)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    try:
        if not COLLECTED_RESULTS:
            return

        use_permanent_db = (
            os.environ.get("ARTHEXIS_TEST_RESULTS_PERMANENT_DB", "0") == "1"
        )
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
        except Exception as exc:  # pragma: no cover - best effort logging
            reporter = session.config.pluginmanager.get_plugin("terminalreporter")
            message = f"Unable to persist test results to primary database: {exc}"
            if reporter:
                reporter.write_line(message, yellow=True)
            else:
                print(message, file=sys.stderr)
    finally:
        from django.db import connections

        connections.close_all()


@pytest.fixture
def anyio_backend():
    """Use asyncio backend for AnyIO tests."""
    return "asyncio"
