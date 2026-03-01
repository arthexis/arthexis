"""Django database helpers and fixtures for pytest."""

from __future__ import annotations

from typing import Any

import pytest


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


@pytest.fixture(scope="session", autouse=True)
def setup_db_for_django_tests(request: pytest.FixtureRequest, django_db_blocker: Any) -> None:
    """Initialize the Django test database once for DB-backed tests."""

    del django_db_blocker
    if not any(requires_db(item) for item in request.session.items):
        return
    request.getfixturevalue("django_db_setup")


@pytest.fixture(scope="session")
def load_sigil_roots_once(django_db_setup: Any, django_db_blocker: Any) -> None:
    """Load SigilRoot fixtures once per session for tests that need the DB."""

    del django_db_setup
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
