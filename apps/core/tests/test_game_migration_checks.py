"""Regression tests for the retired game migration shim safeguards."""

from __future__ import annotations

import pytest
from django.db import connection
from django.db.migrations.recorder import MigrationRecorder

from apps.core import checks

pytestmark = [pytest.mark.django_db(transaction=True)]


@pytest.fixture(autouse=True)
def clear_game_migration_state():
    """Clear game migration recorder rows before and after each test."""

    recorder = MigrationRecorder(connection)
    recorder.ensure_schema()
    recorder.migration_qs.filter(app="game").delete()
    yield
    recorder.migration_qs.filter(app="game").delete()


def test_game_cleanup_check_allows_clean_databases(monkeypatch):
    """Fresh installs should not be blocked once no game state remains."""

    monkeypatch.setattr(
        connection.introspection,
        "table_names",
        lambda cursor=None, **_: [],
    )

    assert checks.game_cleanup_migration_was_applied(None) == []


def test_game_cleanup_check_rejects_partial_migration_state(monkeypatch):
    """Databases stuck on game 0001 must fail fast during upgrades."""

    recorder = MigrationRecorder(connection)
    recorder.record_applied("game", checks.GAME_INITIAL_MIGRATION)
    monkeypatch.setattr(
        connection.introspection,
        "table_names",
        lambda cursor=None, **_: ["django_migrations", checks.LEGACY_GAME_TABLES[0]],
    )

    errors = checks.game_cleanup_migration_was_applied(None)

    assert len(errors) == 1
    assert errors[0].id == "core.E002"
    assert checks.GAME_INITIAL_MIGRATION in errors[0].obj
    assert checks.LEGACY_GAME_TABLES[0] in errors[0].obj


def test_game_cleanup_check_allows_databases_after_cleanup(monkeypatch):
    """Databases that already recorded game 0002 should continue normally."""

    recorder = MigrationRecorder(connection)
    recorder.record_applied("game", checks.GAME_INITIAL_MIGRATION)
    recorder.record_applied("game", checks.GAME_REMOVAL_MIGRATION)
    monkeypatch.setattr(
        connection.introspection,
        "table_names",
        lambda cursor=None, **_: [],
    )

    assert checks.game_cleanup_migration_was_applied(None) == []


def test_game_cleanup_check_honors_selected_database_alias(monkeypatch):
    """System check should inspect the database selected by migration checks."""

    selected_connections = {
        "default": object(),
        "next_line": object(),
    }
    chosen_connections: list[object] = []

    class FakeRecorder:
        def __init__(self, connection):
            chosen_connections.append(connection)

        def has_table(self):
            return False

    monkeypatch.setattr(checks, "connections", selected_connections)
    monkeypatch.setattr(checks, "MigrationRecorder", FakeRecorder)

    assert checks.game_cleanup_migration_was_applied(None, databases=["next_line"]) == []
    assert chosen_connections == [selected_connections["next_line"]]
