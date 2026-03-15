#!/usr/bin/env python3
"""Bootstrap Ada app folders and create merge-friendly migration scripts."""

from __future__ import annotations

import argparse
import datetime as dt
import re
import subprocess
from pathlib import Path

MIGRATION_HEADER_PATTERN = re.compile(r"^--\s*Migration:\s*(?P<id>[\w.-]+)\s*$", re.MULTILINE)
PARENTS_HEADER_PATTERN = re.compile(r"^--\s*Parents:\s*(?P<parents>.*)$", re.MULTILINE)


def _repo_root() -> Path:
    """Return repository root inferred from this script location."""

    return Path(__file__).resolve().parents[1]


def _default_apps_root() -> Path:
    """Return default Ada app root for matrix app scaffolding."""

    return _repo_root() / "ada" / "apps"


def bootstrap_app(app_name: str, apps_root: Path) -> None:
    """Create standard Ada app-matrix folders for one app.

    Args:
        app_name: Name of the target app folder.
        apps_root: Root path containing Ada app directories.
    """

    app_root = apps_root / app_name
    required_dirs = [
        app_root / "functions",
        app_root / "migrations",
        app_root / "models",
        app_root / "templates",
        app_root / "views",
        app_root / "fixtures" / "seed",
    ]
    for path in required_dirs:
        path.mkdir(parents=True, exist_ok=True)
        keep_file = path / ".gitkeep"
        if not keep_file.exists():
            keep_file.write_text("", encoding="utf-8")


def _list_migration_files(app_migrations_dir: Path) -> list[Path]:
    """Return sorted migration SQL files for one app migrations directory."""

    return sorted(path for path in app_migrations_dir.glob("*.sql") if path.is_file())


def _parse_metadata(path: Path) -> tuple[str | None, set[str]]:
    """Extract migration id and parents metadata from one migration SQL file."""

    content = path.read_text(encoding="utf-8")
    migration_match = MIGRATION_HEADER_PATTERN.search(content)
    parents_match = PARENTS_HEADER_PATTERN.search(content)
    migration_id = migration_match.group("id") if migration_match else None
    parents: set[str] = set()
    if parents_match:
        raw = parents_match.group("parents").strip()
        if raw and raw != "-":
            parents = {item.strip() for item in raw.split(",") if item.strip()}
    return migration_id, parents


def _leaf_migration_ids(app_migrations_dir: Path) -> list[str]:
    """Return leaf migration IDs to support merge-friendly parent defaults."""

    migration_ids: set[str] = set()
    referenced_parents: set[str] = set()
    for migration_file in _list_migration_files(app_migrations_dir):
        migration_id, parents = _parse_metadata(migration_file)
        if migration_id:
            migration_ids.add(migration_id)
            referenced_parents.update(parents)
    leafs = sorted(migration_ids - referenced_parents)
    return leafs


def _git_branch() -> str:
    """Return current git branch name for merge-group metadata."""

    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return "detached"
    return result.stdout.strip() or "detached"


def make_migration(app_name: str, migration_name: str, apps_root: Path) -> Path:
    """Create a new migration script for one Ada app with leaf-parent defaults."""

    app_migrations_dir = apps_root / app_name / "migrations"
    app_migrations_dir.mkdir(parents=True, exist_ok=True)

    now = dt.datetime.now(dt.UTC).strftime("%Y%m%d%H%M%S")
    normalized_name = re.sub(r"[^a-zA-Z0-9_]+", "_", migration_name).strip("_")
    migration_id = f"{now}_{normalized_name or 'migration'}"
    parents = _leaf_migration_ids(app_migrations_dir)
    merge_group = _git_branch().replace("/", "-")

    parent_header = ", ".join(parents) if parents else "-"
    path = app_migrations_dir / f"{migration_id}.sql"
    path.write_text(
        "\n".join(
            [
                f"-- Migration: {migration_id}",
                f"-- App: {app_name}",
                f"-- Parents: {parent_header}",
                f"-- Merge-Group: {merge_group}",
                "-- Description: Fill in DDL/DML steps for this migration.",
                "",
                "BEGIN;",
                "-- TODO: add migration SQL here.",
                "COMMIT;",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def _build_parser() -> argparse.ArgumentParser:
    """Build argument parser for Ada matrix utility commands."""

    parser = argparse.ArgumentParser(
        description="Bootstrap Ada app folders and create merge-friendly migrations.",
    )
    parser.add_argument(
        "--apps-root",
        default=str(_default_apps_root()),
        help="Root directory for Ada app folders.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    bootstrap_parser = subparsers.add_parser(
        "bootstrap-app", help="Create folder scaffold for one Ada app.")
    bootstrap_parser.add_argument("app_name", help="Target app folder name.")

    migration_parser = subparsers.add_parser(
        "make-migration", help="Create a merge-friendly migration SQL script.")
    migration_parser.add_argument("app_name", help="Target app folder name.")
    migration_parser.add_argument("migration_name", help="Human-friendly migration name.")

    return parser


def main() -> None:
    """Execute CLI command for Ada matrix scaffolding operations."""

    parser = _build_parser()
    args = parser.parse_args()
    apps_root = Path(args.apps_root)

    if args.command == "bootstrap-app":
        bootstrap_app(args.app_name, apps_root)
        print(f"Bootstrapped Ada app scaffold: {apps_root / args.app_name}")
        return

    if args.command == "make-migration":
        migration_file = make_migration(args.app_name, args.migration_name, apps_root)
        print(f"Created migration: {migration_file}")
        return

    raise ValueError(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    main()
