"""Regression coverage for archived wiki bridge table migration SQL."""

from __future__ import annotations

from importlib import import_module
from types import SimpleNamespace

migration = import_module("apps.app.migrations.0010_archive_wikis_bridge_table")


class StubSchemaEditor:
    """Minimal schema editor stub for asserting quoted SQL output."""

    def __init__(self, tables: list[str]) -> None:
        """Store the visible tables and initialize SQL capture."""

        self.connection = SimpleNamespace(
            introspection=SimpleNamespace(table_names=lambda: tables)
        )
        self.statements: list[str] = []

    def quote_name(self, name: str) -> str:
        """Return a backend-safe quoted identifier for assertions."""

        return f'"{name}"'

    def execute(self, statement: str) -> None:
        """Record SQL statements issued by the migration helper."""

        self.statements.append(statement)


def test_archive_wiki_bridge_table_quotes_identifiers() -> None:
    """Regression: archive helper should quote both source and archive table names."""

    schema_editor = StubSchemaEditor([migration.ARCHIVE_TABLE, migration.SOURCE_TABLE])

    migration.archive_wiki_bridge_table(apps=None, schema_editor=schema_editor)

    assert schema_editor.statements == [
        f'DROP TABLE "{migration.ARCHIVE_TABLE}"',
        (
            f'ALTER TABLE "{migration.SOURCE_TABLE}" '
            f'RENAME TO "{migration.ARCHIVE_TABLE}"'
        ),
    ]


def test_restore_wiki_bridge_table_quotes_identifiers() -> None:
    """Regression: restore helper should quote both source and archive table names."""

    schema_editor = StubSchemaEditor([migration.ARCHIVE_TABLE])

    migration.restore_wiki_bridge_table(apps=None, schema_editor=schema_editor)

    assert schema_editor.statements == [
        (
            f'ALTER TABLE "{migration.ARCHIVE_TABLE}" '
            f'RENAME TO "{migration.SOURCE_TABLE}"'
        )
    ]
