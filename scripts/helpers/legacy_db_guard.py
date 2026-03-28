#!/usr/bin/env python3
"""Detect unsupported legacy SQLite migration histories for 1.0+ workflows."""

from __future__ import annotations

import argparse
import ast
import sqlite3
import sys
from pathlib import Path

LEGACY_BLOCKED_LABELS = {
    "calendars",
    "extensions",
    "fitbit",
    "game",
    "legacy_mermaid",
    "mcp",
    "prompts",
    "prototypes",
    "recipes",
    "screens",
    "selenium",
    "shortcuts",
    "smb",
    "socials",
    "sponsors",
    "survey",
}


def _candidate_app_dirs(repo_root: Path) -> list[Path]:
    apps_dir = repo_root / "apps"
    if not apps_dir.exists():
        return []
    candidates = {
        path.parent
        for path in apps_dir.rglob("apps.py")
        if path.is_file()
    }
    candidates |= {
        path.parent
        for path in apps_dir.rglob("migrations")
        if path.is_dir()
    }
    return sorted(candidates)


def _current_migration_keys(repo_root: Path) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for app_dir in _candidate_app_dirs(repo_root):
        app_label = _app_label_for_dir(app_dir)
        migrations_dir = app_dir / "migrations"
        if not migrations_dir.is_dir():
            continue
        for migration_file in sorted(migrations_dir.glob("*.py")):
            if migration_file.name == "__init__.py":
                continue
            keys.add((app_label, migration_file.stem))
    return keys


def _current_project_labels(repo_root: Path) -> set[str]:
    return {_app_label_for_dir(path) for path in _candidate_app_dirs(repo_root)}


def _app_label_for_dir(app_dir: Path) -> str:
    default_label = app_dir.name
    apps_module = app_dir / "apps.py"
    if not apps_module.is_file():
        return default_label
    try:
        parsed = ast.parse(apps_module.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return default_label

    for node in parsed.body:
        if not isinstance(node, ast.ClassDef):
            continue
        for stmt in node.body:
            if not isinstance(stmt, ast.Assign):
                continue
            if len(stmt.targets) != 1:
                continue
            target = stmt.targets[0]
            if not isinstance(target, ast.Name) or target.id != "label":
                continue
            if isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str):
                return stmt.value.value
    return default_label


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def _applied_migration_keys(db_path: Path) -> set[tuple[str, str]]:
    with sqlite3.connect(db_path) as conn:
        if not _table_exists(conn, "django_migrations"):
            return set()
        rows = conn.execute("SELECT app, name FROM django_migrations").fetchall()
        return {(str(app), str(name)) for app, name in rows}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Fail fast when a SQLite database contains migration entries that are "
            "not part of the current repository migration graph."
        )
    )
    parser.add_argument("--db", required=True, help="Path to sqlite database file")
    parser.add_argument("--repo", required=True, help="Path to repository root")
    parser.add_argument(
        "--max-report",
        type=int,
        default=8,
        help="Maximum number of unknown migration entries to print.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    db_path = Path(args.db)
    repo_root = Path(args.repo)

    if not db_path.exists():
        return 0

    known = _current_migration_keys(repo_root)
    if not known:
        print(
            "Error: Could not detect current migration files under apps/*/migrations. "
            "Cannot perform legacy DB guard check.",
            file=sys.stderr,
        )
        return 1

    try:
        applied = _applied_migration_keys(db_path)
    except sqlite3.Error as exc:
        print(
            f"Error: Could not read django_migrations from {db_path}: {exc}",
            file=sys.stderr,
        )
        print("Cannot perform legacy DB guard check.", file=sys.stderr)
        return 1

    project_labels = _current_project_labels(repo_root)
    unknown = sorted(
        (app_label, migration_name)
        for app_label, migration_name in applied - known
        if app_label in project_labels or app_label in LEGACY_BLOCKED_LABELS
    )
    if not unknown:
        return 0

    print("Unsupported legacy migration path detected in existing database.", file=sys.stderr)
    print(
        "This repository now supports fresh-install + data-import workflows for 1.0+.",
        file=sys.stderr,
    )
    print("Unknown migration entries (sample):", file=sys.stderr)
    for app_label, migration_name in unknown[: max(1, args.max_report)]:
        print(f"  - {app_label}.{migration_name}", file=sys.stderr)
    if len(unknown) > max(1, args.max_report):
        remaining = len(unknown) - max(1, args.max_report)
        print(f"  ... and {remaining} more", file=sys.stderr)

    print(
        "Action: reinstall on a fresh database, then import data using the operator runbook.",
        file=sys.stderr,
    )
    print(
        "Runbook: docs/operations/reinstall-data-import-runbook.md",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
