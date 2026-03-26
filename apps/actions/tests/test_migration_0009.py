"""Regression tests for actions migration 0009 helpers."""

from __future__ import annotations

import importlib

from django.db import connection
import pytest

migration = importlib.import_module(
    "apps.actions.migrations.0009_remove_remoteactiontoken_user_and_more"
)

class _SchemaEditorStub:
    """Minimal schema editor stub for migration helper tests."""

    connection = connection

    def execute(self, sql, params=None):
        """Execute the given SQL using the shared test connection."""

        with connection.cursor() as cursor:
            cursor.execute(sql, params or [])

@pytest.mark.django_db
def test_clear_archive_tables_removes_prior_archived_rows():
    """Regression: reapplying migration 0009 should start with empty archive tables."""

    schema_editor = _SchemaEditorStub()

    with connection.cursor() as cursor:
        for table_name in (
            migration.REMOTE_ACTION_ARCHIVE_TABLE,
            migration.REMOTE_ACTION_TOKEN_ARCHIVE_TABLE,
            migration.DASHBOARD_ACTION_ARCHIVE_TABLE,
        ):
            cursor.execute(f"DROP TABLE IF EXISTS {table_name}")

        cursor.execute(
            f"CREATE TABLE {migration.REMOTE_ACTION_ARCHIVE_TABLE} (id INTEGER PRIMARY KEY, archived_at TEXT)"
        )
        cursor.execute(
            f"CREATE TABLE {migration.REMOTE_ACTION_TOKEN_ARCHIVE_TABLE} (id INTEGER PRIMARY KEY, archived_at TEXT)"
        )
        cursor.execute(
            f'CREATE TABLE {migration.DASHBOARD_ACTION_ARCHIVE_TABLE} (id INTEGER PRIMARY KEY, "order" INTEGER, archived_at TEXT)'
        )
        cursor.execute(
            f"INSERT INTO {migration.REMOTE_ACTION_ARCHIVE_TABLE} (id, archived_at) VALUES (1, CURRENT_TIMESTAMP)"
        )
        cursor.execute(
            f"INSERT INTO {migration.REMOTE_ACTION_TOKEN_ARCHIVE_TABLE} (id, archived_at) VALUES (1, CURRENT_TIMESTAMP)"
        )
        cursor.execute(
            f'INSERT INTO {migration.DASHBOARD_ACTION_ARCHIVE_TABLE} (id, "order", archived_at) VALUES (1, 1, CURRENT_TIMESTAMP)'
        )

    migration._clear_archive_tables(schema_editor)

    with connection.cursor() as cursor:
        for table_name in (
            migration.REMOTE_ACTION_ARCHIVE_TABLE,
            migration.REMOTE_ACTION_TOKEN_ARCHIVE_TABLE,
            migration.DASHBOARD_ACTION_ARCHIVE_TABLE,
        ):
            cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
            assert cursor.fetchone()[0] == 0
