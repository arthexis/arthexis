"""Regression tests for the retired Fitbit migration shim safeguards."""

from __future__ import annotations

import pytest
from django.db import connection
from django.db.migrations.recorder import MigrationRecorder

from apps.core import checks

pytestmark = [pytest.mark.django_db(transaction=True)]

@pytest.fixture(autouse=True)
def clear_fitbit_migration_state():
    """Clear Fitbit migration recorder rows before and after each test.

    Parameters:
        None.

    Returns:
        None: The fixture isolates mutation of the migration recorder table.

    Raises:
        None.
    """

    recorder = MigrationRecorder(connection)
    recorder.ensure_schema()
    recorder.migration_qs.filter(app="fitbit").delete()
    yield
    recorder.migration_qs.filter(app="fitbit").delete()

def test_fitbit_cleanup_check_allows_clean_databases(monkeypatch):
    """Fresh installs should not be blocked once no Fitbit state remains.

    Parameters:
        monkeypatch: Pytest fixture used to stub table introspection.

    Returns:
        None: Assertions verify the check returns no errors.

    Raises:
        AssertionError: If the clean baseline is rejected.
    """

    monkeypatch.setattr(
        connection.introspection,
        "table_names",
        lambda cursor=None, **_: [],
    )

    assert checks.fitbit_cleanup_migration_was_applied(None) == []

def test_fitbit_cleanup_check_rejects_partial_migration_state(monkeypatch):
    """Databases stuck on Fitbit 0001 must fail fast during upgrades.

    Parameters:
        monkeypatch: Pytest fixture used to stub table introspection.

    Returns:
        None: Assertions verify an actionable system-check error is returned.

    Raises:
        AssertionError: If the partial historical state is not rejected.
    """

    recorder = MigrationRecorder(connection)
    recorder.record_applied("fitbit", checks.FITBIT_INITIAL_MIGRATION)
    monkeypatch.setattr(
        connection.introspection,
        "table_names",
        lambda cursor=None, **_: ["django_migrations", checks.LEGACY_FITBIT_TABLES[0]],
    )

    errors = checks.fitbit_cleanup_migration_was_applied(None)

    assert len(errors) == 1
    assert errors[0].id == "core.E001"
    assert checks.FITBIT_INITIAL_MIGRATION in errors[0].obj
    assert checks.LEGACY_FITBIT_TABLES[0] in errors[0].obj

def test_fitbit_cleanup_check_allows_databases_after_cleanup(monkeypatch):
    """Databases that already recorded Fitbit 0002 should continue normally.

    Parameters:
        monkeypatch: Pytest fixture used to stub table introspection.

    Returns:
        None: Assertions verify the system check passes.

    Raises:
        AssertionError: If the completed migration chain is rejected.
    """

    recorder = MigrationRecorder(connection)
    recorder.record_applied("fitbit", checks.FITBIT_INITIAL_MIGRATION)
    recorder.record_applied("fitbit", checks.FITBIT_REMOVAL_MIGRATION)
    monkeypatch.setattr(
        connection.introspection,
        "table_names",
        lambda cursor=None, **_: [],
    )

    assert checks.fitbit_cleanup_migration_was_applied(None) == []

