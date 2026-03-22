"""Django database helpers and fixtures for pytest."""

from __future__ import annotations

from typing import Any

import pytest


_PYTEST_DJANGO_MISSING_ERROR = (
    "Database-backed tests require pytest-django. Install test dependencies "
    "(for example: `pip install -r requirements-test.txt` or "
    "`pip install -r requirements-ci.txt`) before running pytest."
)


def _require_pytest_django_fixture(request: pytest.FixtureRequest, fixture_name: str) -> Any:
    """Return a pytest-django fixture or raise a clear usage error when unavailable."""

    try:
        return request.getfixturevalue(fixture_name)
    except pytest.FixtureLookupError as exc:
        raise pytest.UsageError(_PYTEST_DJANGO_MISSING_ERROR) from exc


def requires_db(item: pytest.Item) -> bool:
    """Return ``True`` when a test item needs database access."""

    if item.get_closest_marker("django_db") is not None:
        return True
    if {"db", "transactional_db"}.intersection(item.fixturenames):
        return True
    test_class = getattr(item, "cls", None)
    if test_class is None:
        return False

    from django.test import TransactionTestCase

    return issubclass(test_class, TransactionTestCase)


def _ensure_pytest_django_plugin_for_db_items(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    """Fail fast when database tests are collected without pytest-django.

    Raising once at collection time avoids repeating the same fixture lookup
    error for every database-backed test in the suite.
    """

    if not any(requires_db(item) for item in items):
        return
    if config.pluginmanager.hasplugin("django"):
        return
    raise pytest.UsageError(_PYTEST_DJANGO_MISSING_ERROR)


def pytest_collection_finish(session: pytest.Session) -> None:
    """Validate required pytest plugins after deselection is finalized."""

    _ensure_pytest_django_plugin_for_db_items(session.config, session.items)


@pytest.fixture(scope="session", autouse=True)
def setup_db_for_django_tests(request: pytest.FixtureRequest) -> None:
    """Initialize the Django test database once for DB-backed tests."""

    if not any(requires_db(item) for item in request.session.items):
        return
    _require_pytest_django_fixture(request, "django_db_setup")


@pytest.fixture(scope="session")
def load_sigil_roots_once(request: pytest.FixtureRequest) -> None:
    """Load SigilRoot fixtures once per session for tests that need the DB."""

    _require_pytest_django_fixture(request, "django_db_setup")
    django_db_blocker = _require_pytest_django_fixture(request, "django_db_blocker")
    from apps.sigils.loader import load_fixture_sigil_roots

    with django_db_blocker.unblock():
        load_fixture_sigil_roots(using="default")


@pytest.fixture(autouse=True)
def ensure_fixture_sigil_roots(request: pytest.FixtureRequest) -> None:
    """Auto-load sigil roots for tests marked with ``sigil_roots`` and DB usage."""

    if requires_db(request.node) and request.node.get_closest_marker("sigil_roots"):
        request.getfixturevalue("load_sigil_roots_once")


@pytest.fixture
def sigil_roots(request: pytest.FixtureRequest) -> None:
    """Explicit fixture alias for loading ``SigilRoot`` fixture records once."""

    request.getfixturevalue("load_sigil_roots_once")


@pytest.fixture
def anyio_backend() -> str:
    """Use asyncio backend for AnyIO tests."""

    return "asyncio"
