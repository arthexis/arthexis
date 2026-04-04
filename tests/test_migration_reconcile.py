from __future__ import annotations

import sqlite3

import pytest
from pathlib import Path

from scripts.helpers.migration_reconcile import reconcile_sqlite_tables


def _exec_many(db_path: Path, statements: list[str]) -> None:
    with sqlite3.connect(db_path) as conn:
        for statement in statements:
            conn.execute(statement)
        conn.commit()


def _sqlite_identifier(name: str) -> str:
    return f'"{name.replace(chr(34), chr(34) * 2)}"'


@pytest.mark.critical
def test_reconcile_copies_only_common_tables(tmp_path: Path) -> None:
    source = tmp_path / "source.sqlite3"
    target = tmp_path / "target.sqlite3"

    _exec_many(
        source,
        [
            "CREATE TABLE keep_me (id INTEGER PRIMARY KEY, name TEXT, legacy TEXT)",
            "INSERT INTO keep_me (id, name, legacy) VALUES (1, 'Alpha', 'legacy')",
            "CREATE TABLE source_only (id INTEGER PRIMARY KEY, value TEXT)",
            "INSERT INTO source_only (id, value) VALUES (1, 'old')",
        ],
    )
    _exec_many(
        target,
        [
            "CREATE TABLE keep_me (id INTEGER PRIMARY KEY, name TEXT, current TEXT)",
            "CREATE TABLE target_only (id INTEGER PRIMARY KEY, value TEXT)",
        ],
    )

    report = reconcile_sqlite_tables(source, target)

    assert report.copied_tables == ["keep_me"]
    assert report.missing_in_source == ["target_only"]
    assert report.missing_in_target == ["source_only"]
    assert report.skipped_tables == {}

    with sqlite3.connect(target) as conn:
        rows = conn.execute("SELECT id, name, current FROM keep_me").fetchall()

    assert rows == [(1, "Alpha", None)]


def test_reconcile_skips_tables_without_compatible_columns(tmp_path: Path) -> None:
    source = tmp_path / "source.sqlite3"
    target = tmp_path / "target.sqlite3"

    _exec_many(
        source,
        [
            "CREATE TABLE incompatible (legacy_id INTEGER PRIMARY KEY, value TEXT)",
            "INSERT INTO incompatible (legacy_id, value) VALUES (1, 'old')",
        ],
    )
    _exec_many(
        target,
        [
            "CREATE TABLE incompatible (id INTEGER PRIMARY KEY, payload TEXT)",
        ],
    )

    report = reconcile_sqlite_tables(source, target)

    assert report.copied_tables == []
    assert report.missing_in_source == []
    assert report.missing_in_target == []
    assert report.skipped_tables == {"incompatible": "no common columns"}


def test_reconcile_handles_tables_with_quotes_in_name(tmp_path: Path) -> None:
    source = tmp_path / "source.sqlite3"
    target = tmp_path / "target.sqlite3"
    weird_table = 'odd"\'table'

    _exec_many(
        source,
        [
            f"CREATE TABLE {_sqlite_identifier(weird_table)} (id INTEGER PRIMARY KEY, name TEXT)",
            f"INSERT INTO {_sqlite_identifier(weird_table)} (id, name) VALUES (1, 'Alpha')",
        ],
    )
    _exec_many(
        target,
        [
            f"CREATE TABLE {_sqlite_identifier(weird_table)} (id INTEGER PRIMARY KEY, name TEXT)",
        ],
    )

    report = reconcile_sqlite_tables(source, target)

    assert report.copied_tables == [weird_table]
    with sqlite3.connect(target) as conn:
        rows = conn.execute(
            f"SELECT id, name FROM {_sqlite_identifier(weird_table)}"
        ).fetchall()
    assert rows == [(1, "Alpha")]
