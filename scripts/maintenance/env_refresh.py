#!/usr/bin/env python
"""Development maintenance entrypoint.

The implementation is being decomposed incrementally from ``env_refresh_core``.
This facade preserves the historical import surface while routing extracted
fixture helpers through their dedicated module.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from django.core.management.base import CommandError

from scripts.maintenance import env_refresh_core as _core
from scripts.maintenance.env_refresh_fixtures import (
    close_old_connections_safely,
    fixture_files,
    fixture_hashes_by_app,
    fixture_load_prescan,
    fixture_mtime_cache,
    fixture_sort_key,
    fixture_tables,
    fixtures_hash,
    load_fixture_with_retry,
    load_fixtures_with_deferred_retry,
)

# Preserve the legacy module surface while the implementation is split into
# focused modules. This also keeps existing tests that monkeypatch private
# helpers through ``scripts.maintenance.env_refresh`` working.
_COMPAT_NAMES = tuple(
    name
    for name in vars(_core)
    if not name.startswith("__") and name not in {"run_database_tasks", "main", "TASKS"}
)
for _name in _COMPAT_NAMES:
    globals()[_name] = getattr(_core, _name)

# Route the extracted fixture responsibilities through the dedicated module.
_close_old_connections_safely = close_old_connections_safely
_fixture_files = fixture_files
_fixture_mtime_cache = fixture_mtime_cache
_fixture_tables = fixture_tables
_fixture_load_prescan = fixture_load_prescan
_load_fixture_with_retry = load_fixture_with_retry
_fixture_sort_key = fixture_sort_key
_load_fixtures_with_deferred_retry = load_fixtures_with_deferred_retry
_fixtures_hash = fixtures_hash
_fixture_hashes_by_app = fixture_hashes_by_app


def _sync_core_overrides() -> None:
    """Propagate facade monkeypatches and extracted helpers into the legacy core."""

    for name in _COMPAT_NAMES:
        if name in globals():
            setattr(_core, name, globals()[name])


def run_database_tasks(*args: Any, **kwargs: Any) -> None:
    """Run database maintenance through the decomposed implementation."""

    _sync_core_overrides()
    return _core.run_database_tasks(*args, **kwargs)


TASKS = {"database": run_database_tasks}


def main(
    selected: list[str] | None = None,
    *,
    latest: bool = False,
    clean: bool = False,
    force_db: bool = False,
    migrate_reconcile: bool = False,
    auto_reconcile_on_mismatch: bool = False,
    write_migrations: bool = False,
) -> None:
    """Run the selected maintenance tasks."""

    to_run = selected or list(TASKS)
    for name in to_run:
        TASKS[name](
            latest=latest,
            clean=clean,
            force_db=force_db,
            migrate_reconcile=migrate_reconcile,
            auto_reconcile_on_mismatch=auto_reconcile_on_mismatch,
            write_migrations=write_migrations,
        )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Development maintenance tasks")
    parser.add_argument("tasks", nargs="*", choices=TASKS.keys(), help="Tasks to run")
    parser.add_argument(
        "--latest", action="store_true", help="Force rebuild if migrations changed"
    )
    parser.add_argument(
        "--clean", action="store_true", help="Reset database before applying migrations"
    )
    parser.add_argument(
        "--force-db",
        action="store_true",
        help="Force running migrations and fixtures even if preflight is clean",
    )
    parser.add_argument(
        "--migrate",
        action="store_true",
        help=(
            "Rebuild the database and reconcile compatible rows from a pre-migration "
            "SQLite or PostgreSQL snapshot while ignoring incompatible structures."
        ),
    )
    parser.add_argument(
        "--reconcile",
        action="store_true",
        help=(
            "When migration graph/version mismatches are detected, automatically "
            "retry with reconciliation enabled."
        ),
    )
    parser.add_argument(
        "--write-migrations",
        action="store_true",
        help=(
            "Allow env-refresh to write migration files. This is intended for "
            "development checkouts only; deployed nodes should consume tracked "
            "migrations from source control."
        ),
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    try:
        main(
            args.tasks,
            latest=args.latest,
            clean=args.clean,
            force_db=args.force_db,
            migrate_reconcile=args.migrate,
            auto_reconcile_on_mismatch=args.reconcile,
            write_migrations=args.write_migrations,
        )
    except CommandError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
