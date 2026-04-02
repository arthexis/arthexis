#!/usr/bin/env python
"""PostgreSQL migration reconciliation helpers for major-version upgrades."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql

from scripts.helpers.migration_reconcile_common import ReconcileReport

_SKIP_TABLES = {"django_migrations"}
_BATCH_SIZE = 500


def _connection_kwargs(
    default_db: dict[str, Any], *, dbname: str | None = None
) -> dict[str, Any]:
    return {
        "dbname": dbname or default_db["NAME"],
        "user": default_db.get("USER") or None,
        "password": default_db.get("PASSWORD") or None,
        "host": default_db.get("HOST") or None,
        "port": default_db.get("PORT") or None,
    }


def _cleanup_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in kwargs.items() if value not in {None, ""}}


def backup_postgres_database(default_db: dict[str, Any], destination_dir: Path) -> Path:
    """Create a deterministic ``pg_dump`` snapshot for reconciliation."""

    destination_dir.mkdir(parents=True, exist_ok=True)
    db_name = default_db["NAME"]
    backup_path = destination_dir / f"{db_name}.pre_major_migrate.dump"

    command = [
        "pg_dump",
        "--format=custom",
        "--file",
        str(backup_path),
    ]

    if default_db.get("HOST"):
        command.extend(["--host", default_db["HOST"]])
    if default_db.get("PORT"):
        command.extend(["--port", str(default_db["PORT"])])
    if default_db.get("USER"):
        command.extend(["--username", default_db["USER"]])

    command.append(db_name)

    env = os.environ.copy()
    if default_db.get("PASSWORD"):
        env["PGPASSWORD"] = default_db["PASSWORD"]

    subprocess.run(command, check=True, env=env)
    return backup_path


def _run_psql_command(command: list[str], *, default_db: dict[str, Any]) -> None:
    env = os.environ.copy()
    if default_db.get("PASSWORD"):
        env["PGPASSWORD"] = default_db["PASSWORD"]
    subprocess.run(command, check=True, env=env)


def _table_names(conn: psycopg.Connection[Any]) -> set[str]:
    rows = conn.execute(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
        """
    ).fetchall()
    return {row[0] for row in rows}


def _column_names(conn: psycopg.Connection[Any], table: str) -> list[str]:
    rows = conn.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        ORDER BY ordinal_position
        """,
        (table,),
    ).fetchall()
    return [row[0] for row in rows]


def _conflict_columns(conn: psycopg.Connection[Any], table: str) -> list[str]:
    row = conn.execute(
        """
        SELECT i.indisprimary, array_agg(a.attname ORDER BY ordinality)
        FROM pg_index i
        CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS k(attnum, ordinality)
        JOIN pg_class c ON c.oid = i.indrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = k.attnum
        WHERE n.nspname = 'public' AND c.relname = %s AND (i.indisprimary OR i.indisunique)
        GROUP BY i.indexrelid, i.indisprimary
        ORDER BY i.indisprimary DESC
        LIMIT 1
        """,
        (table,),
    ).fetchone()
    if not row:
        return []
    return list(row[1] or [])


def _batched_rows(
    rows: list[tuple[Any, ...]], size: int = _BATCH_SIZE
) -> Iterator[list[tuple[Any, ...]]]:
    for index in range(0, len(rows), size):
        yield rows[index : index + size]


def _copy_table_rows(
    *,
    source_conn: psycopg.Connection[Any],
    target_conn: psycopg.Connection[Any],
    table: str,
    columns: list[str],
) -> tuple[int, int]:
    quoted_table = sql.Identifier(table)
    quoted_columns = sql.SQL(", ").join(sql.Identifier(column) for column in columns)

    source_rows = source_conn.execute(
        sql.SQL("SELECT {} FROM public.{}").format(quoted_columns, quoted_table)
    ).fetchall()
    source_count = len(source_rows)

    if not source_rows:
        return 0, 0

    conflict_columns = _conflict_columns(target_conn, table)
    if conflict_columns:
        conflict_sql = sql.SQL("ON CONFLICT ({}) DO NOTHING").format(
            sql.SQL(", ").join(sql.Identifier(column) for column in conflict_columns)
        )
    else:
        conflict_sql = sql.SQL("ON CONFLICT DO NOTHING")

    inserted = 0
    for batch in _batched_rows(source_rows):
        values_sql = sql.SQL(", ").join(
            sql.SQL("({})").format(
                sql.SQL(", ").join(sql.Placeholder() for _ in columns)
            )
            for _ in batch
        )
        params: list[Any] = [value for row in batch for value in row]

        insert_sql = sql.SQL(
            "INSERT INTO public.{} ({}) VALUES {} {} RETURNING 1"
        ).format(quoted_table, quoted_columns, values_sql, conflict_sql)

        inserted += len(target_conn.execute(insert_sql, params).fetchall())

    return inserted, source_count


def restore_postgres_snapshot_to_temp_database(
    *,
    snapshot_path: Path,
    default_db: dict[str, Any],
    temp_db_name: str,
) -> None:
    """Restore the snapshot into ``temp_db_name`` for row reconciliation."""

    admin_command = ["psql"]
    if default_db.get("HOST"):
        admin_command.extend(["--host", default_db["HOST"]])
    if default_db.get("PORT"):
        admin_command.extend(["--port", str(default_db["PORT"])])
    if default_db.get("USER"):
        admin_command.extend(["--username", default_db["USER"]])

    admin_command.extend(
        [
            "--dbname",
            "postgres",
            "--command",
            (
                f"DROP DATABASE IF EXISTS \"{temp_db_name}\"; "
                f"CREATE DATABASE \"{temp_db_name}\";"
            ),
        ]
    )
    _run_psql_command(admin_command, default_db=default_db)

    restore_command = [
        "pg_restore",
        "--clean",
        "--if-exists",
        "--no-owner",
        "--no-privileges",
        "--dbname",
        temp_db_name,
    ]
    if default_db.get("HOST"):
        restore_command.extend(["--host", default_db["HOST"]])
    if default_db.get("PORT"):
        restore_command.extend(["--port", str(default_db["PORT"])])
    if default_db.get("USER"):
        restore_command.extend(["--username", default_db["USER"]])

    restore_command.append(str(snapshot_path))
    _run_psql_command(restore_command, default_db=default_db)


def drop_postgres_temp_database(*, default_db: dict[str, Any], temp_db_name: str) -> None:
    """Drop the temporary reconciliation database if present."""

    admin_command = ["psql"]
    if default_db.get("HOST"):
        admin_command.extend(["--host", default_db["HOST"]])
    if default_db.get("PORT"):
        admin_command.extend(["--port", str(default_db["PORT"])])
    if default_db.get("USER"):
        admin_command.extend(["--username", default_db["USER"]])

    admin_command.extend(
        [
            "--dbname",
            "postgres",
            "--command",
            f"DROP DATABASE IF EXISTS \"{temp_db_name}\" WITH (FORCE);",
        ]
    )
    _run_psql_command(admin_command, default_db=default_db)


def reconcile_postgres_tables(
    *,
    source_db_name: str,
    target_db: dict[str, Any],
) -> ReconcileReport:
    """Copy compatible rows from ``source_db_name`` into the target database."""

    copied_tables: list[str] = []
    skipped_tables: dict[str, str] = {}
    skipped_columns: dict[str, list[str]] = {}
    skipped_rows: dict[str, int] = {}

    with (
        psycopg.connect(
            **_cleanup_kwargs(_connection_kwargs(target_db, dbname=source_db_name))
        ) as source_conn,
        psycopg.connect(**_cleanup_kwargs(_connection_kwargs(target_db))) as target_conn,
    ):
        source_tables = _table_names(source_conn)
        target_tables = _table_names(target_conn)

        common_tables = sorted((source_tables & target_tables) - _SKIP_TABLES)
        missing_in_source = sorted(target_tables - source_tables - _SKIP_TABLES)
        missing_in_target = sorted(source_tables - target_tables - _SKIP_TABLES)

        for table in common_tables:
            source_columns = set(_column_names(source_conn, table))
            target_columns = _column_names(target_conn, table)
            compatible_columns = [
                column for column in target_columns if column in source_columns
            ]

            if not compatible_columns:
                skipped_tables[table] = "no common columns"
                continue

            target_only_columns = sorted(set(target_columns) - source_columns)
            if target_only_columns:
                skipped_columns[table] = target_only_columns

            try:
                inserted_count, source_count = _copy_table_rows(
                    source_conn=source_conn,
                    target_conn=target_conn,
                    table=table,
                    columns=compatible_columns,
                )
            except psycopg.Error as exc:
                skipped_tables[table] = str(exc)
                target_conn.rollback()
                continue

            copied_tables.append(table)
            skipped = max(source_count - inserted_count, 0)
            if skipped:
                skipped_rows[table] = skipped

        target_conn.commit()

    return ReconcileReport(
        backend="postgresql",
        copied_tables=copied_tables,
        missing_in_source=missing_in_source,
        missing_in_target=missing_in_target,
        skipped_tables=skipped_tables,
        skipped_columns=skipped_columns,
        skipped_rows=skipped_rows,
    )
