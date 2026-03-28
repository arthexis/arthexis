#!/usr/bin/env python
"""SQLite migration reconciliation helpers for major-version upgrades."""

from __future__ import annotations

import argparse
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path


_SKIP_TABLES = {"django_migrations", "sqlite_sequence"}


@dataclass(slots=True)
class ReconcileReport:
    """Summary of copied and skipped SQLite tables."""

    copied_tables: list[str]
    missing_in_source: list[str]
    missing_in_target: list[str]
    skipped_tables: dict[str, str]



def _table_names(conn: sqlite3.Connection, *, database: str = "main") -> set[str]:
    rows = conn.execute(
        "SELECT name FROM "
        f"{database}.sqlite_master "
        "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {row[0] for row in rows}



def _quote_identifier(identifier: str) -> str:
    escaped = identifier.replace('"', '""')
    return f'"{escaped}"'


def _column_names(
    conn: sqlite3.Connection, table: str, *, database: str = "main"
) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM pragma_table_info(?, ?)",
        (table, database),
    ).fetchall()
    return [row[0] for row in rows]



def reconcile_sqlite_tables(source_db: Path, target_db: Path) -> ReconcileReport:
    """Copy common tables from *source_db* into *target_db*.

    Missing tables are ignored and per-table copy failures are recorded instead of
    aborting the reconciliation run.
    """

    copied_tables: list[str] = []
    skipped_tables: dict[str, str] = {}

    with sqlite3.connect(target_db) as target_conn:
        target_conn.row_factory = sqlite3.Row
        target_tables = _table_names(target_conn)
        target_conn.execute("ATTACH DATABASE ? AS source_db", (str(source_db),))
        source_tables = _table_names(target_conn, database="source_db")

        common_tables = sorted((source_tables & target_tables) - _SKIP_TABLES)
        missing_in_source = sorted(target_tables - source_tables - _SKIP_TABLES)
        missing_in_target = sorted(source_tables - target_tables - _SKIP_TABLES)

        for table in common_tables:
            source_columns = set(_column_names(target_conn, table, database="source_db"))
            target_columns = [
                column
                for column in _column_names(target_conn, table)
                if column in source_columns
            ]

            if not target_columns:
                skipped_tables[table] = "no common columns"
                continue

            quoted_table = _quote_identifier(table)
            quoted_columns = ", ".join(
                _quote_identifier(column) for column in target_columns
            )
            statement = (
                f"INSERT OR IGNORE INTO {quoted_table} ({quoted_columns}) "
                f"SELECT {quoted_columns} FROM source_db.{quoted_table}"
            )

            try:
                target_conn.execute(statement)
            except sqlite3.DatabaseError as exc:
                skipped_tables[table] = str(exc)
                continue

            copied_tables.append(table)

        target_conn.commit()
        target_conn.execute("DETACH DATABASE source_db")

    return ReconcileReport(
        copied_tables=copied_tables,
        missing_in_source=missing_in_source,
        missing_in_target=missing_in_target,
        skipped_tables=skipped_tables,
    )



def backup_sqlite_database(db_path: Path, destination_dir: Path) -> Path:
    """Create a timestamp-free backup path for deterministic automation."""

    destination_dir.mkdir(parents=True, exist_ok=True)
    backup_path = destination_dir / f"{db_path.stem}.pre_major_migrate{db_path.suffix}"
    shutil.copy2(db_path, backup_path)
    return backup_path



def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Copy rows from a legacy SQLite database into a freshly migrated "
            "database while ignoring missing tables."
        )
    )
    parser.add_argument("--source", required=True, help="Path to legacy SQLite database")
    parser.add_argument("--target", required=True, help="Path to fresh SQLite database")
    return parser.parse_args()



def main() -> int:
    args = _parse_args()
    report = reconcile_sqlite_tables(Path(args.source), Path(args.target))

    print(f"Copied tables: {len(report.copied_tables)}")
    if report.missing_in_source:
        print(f"Missing in source (ignored): {', '.join(report.missing_in_source)}")
    if report.missing_in_target:
        print(f"Missing in target (ignored): {', '.join(report.missing_in_target)}")
    if report.skipped_tables:
        print("Skipped tables:")
        for table, reason in sorted(report.skipped_tables.items()):
            print(f"  - {table}: {reason}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
