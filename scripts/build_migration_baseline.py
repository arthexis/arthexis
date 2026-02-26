#!/usr/bin/env python3
"""Build migration baselines for high-churn Django apps.

This release-time helper detects deep migration chains and runs
``manage.py squashmigrations`` for apps that cross a configured threshold.
It also validates that generated squashed migrations have explicit
``replaces`` coverage and do not keep stale self-dependencies.
"""

from __future__ import annotations

import argparse
import ast
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_APPS = ("nodes", "features", "ocpp", "links")
DEFAULT_THRESHOLD = 12
DEFAULT_RECENT_SQUASH_WINDOW = 3
MIGRATION_PATTERN = re.compile(r"^(\d{4})_(.+)\.py$")


class MigrationBaselineError(RuntimeError):
    """Raised when a baseline operation cannot proceed safely."""


@dataclass(frozen=True)
class MigrationFileInfo:
    """Metadata extracted from a migration filename."""

    name: str
    number: int
    path: Path


@dataclass(frozen=True)
class AppBaselineStatus:
    """Computed baseline status for a single app."""

    app_label: str
    migration_names: tuple[str, ...]
    active_chain_depth: int
    latest_number: int
    latest_squash_number: int | None
    squash_target: str | None
    squash_start: str | None

    def exceeds_threshold(self, threshold: int) -> bool:
        """Return ``True`` when this app needs baseline work for ``threshold``."""

        return self.active_chain_depth > threshold

    def has_recent_squash(self, recent_window: int) -> bool:
        """Return whether a squash marker is recent enough for the configured window."""

        if self.latest_squash_number is None:
            return False
        return self.latest_squash_number >= (self.latest_number - recent_window)


def _migration_attributes(path: Path) -> dict[str, Any]:
    """Extract literal Migration class attributes from a migration file."""

    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        raise MigrationBaselineError(f"Unable to parse migration file {path.name}: {exc}") from exc
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != "Migration":
            continue
        attrs: dict[str, Any] = {}
        for stmt in node.body:
            if not isinstance(stmt, ast.Assign):
                continue
            if len(stmt.targets) != 1 or not isinstance(stmt.targets[0], ast.Name):
                continue
            name = stmt.targets[0].id
            if name not in {"replaces", "dependencies"}:
                continue
            try:
                attrs[name] = ast.literal_eval(stmt.value)
            except (ValueError, SyntaxError):
                attrs[name] = []
        return attrs
    return {}


def _migration_files(app_label: str, *, repo_root: Path = REPO_ROOT) -> list[MigrationFileInfo]:
    """Return migration files for an app sorted by migration number and filename."""

    migration_dir = repo_root / "apps" / app_label / "migrations"
    if not migration_dir.exists():
        raise MigrationBaselineError(f"Migration directory not found for app '{app_label}'.")

    files: list[MigrationFileInfo] = []
    for path in migration_dir.glob("*.py"):
        match = MIGRATION_PATTERN.match(path.name)
        if not match:
            continue
        files.append(MigrationFileInfo(path.stem, int(match.group(1)), path))

    files.sort(key=lambda info: (info.number, info.name))
    return files


def evaluate_app_baseline(app_label: str, *, repo_root: Path = REPO_ROOT) -> AppBaselineStatus:
    """Evaluate migration depth and squash eligibility for a single app."""

    files = _migration_files(app_label, repo_root=repo_root)
    replaces: set[str] = set()
    squash_numbers: list[int] = []

    for info in files:
        attrs = _migration_attributes(info.path)
        replaces_entries = attrs.get("replaces", [])
        if replaces_entries:
            squash_numbers.append(info.number)
            for replace_app, replace_name in replaces_entries:
                if replace_app == app_label:
                    replaces.add(replace_name)

    active = [info for info in files if info.name not in replaces]
    latest = active[-1] if active else None
    latest_non_squashed = next(
        (info for info in reversed(active) if "squashed" not in info.name),
        None,
    )
    squashed_cutoff = max(squash_numbers) if squash_numbers else None
    if squashed_cutoff is None:
        start_candidate = next((info for info in active if "squashed" not in info.name), None)
    else:
        start_candidate = next(
            (
                info
                for info in active
                if info.number > squashed_cutoff and "squashed" not in info.name
            ),
            None,
        )

    return AppBaselineStatus(
        app_label=app_label,
        migration_names=tuple(info.name for info in files),
        active_chain_depth=len(active),
        latest_number=(latest.number if latest else 0),
        latest_squash_number=squashed_cutoff,
        squash_target=(latest_non_squashed.name if latest_non_squashed else None),
        squash_start=(start_candidate.name if start_candidate else None),
    )


def _run_manage(*args: str, repo_root: Path = REPO_ROOT) -> subprocess.CompletedProcess[str]:
    """Run a manage.py command and return the completed process."""

    return subprocess.run(
        [sys.executable, "manage.py", *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )


def _validate_squashed_file(app_label: str, squashed_file: Path) -> None:
    """Validate that a generated squash file has safe replacement metadata."""

    source = squashed_file.read_text(encoding="utf-8")
    if "need manual copying" in source:
        raise MigrationBaselineError(
            "Generated squash migration requires manual data-migration copying: "
            f"{squashed_file.name}"
        )

    attrs = _migration_attributes(squashed_file)
    replaces_entries = attrs.get("replaces", [])
    if not replaces_entries:
        raise MigrationBaselineError(
            f"Generated squash migration is missing replaces entries: {squashed_file.name}"
        )

    replaced_local = [name for replace_app, name in replaces_entries if replace_app == app_label]
    if not replaced_local:
        raise MigrationBaselineError(
            f"Generated squash migration does not replace local migrations: {squashed_file.name}"
        )

    dependencies = attrs.get("dependencies", [])
    stale_dependencies = [
        dep_name
        for dep_app, dep_name in dependencies
        if dep_app == app_label and dep_name in replaced_local
    ]
    if stale_dependencies:
        raise MigrationBaselineError(
            "Generated squash migration keeps stale local dependencies: "
            f"{', '.join(sorted(stale_dependencies))}"
        )

def squash_app_to_target(app_label: str, start: str, target: str, *, repo_root: Path = REPO_ROOT) -> Path:
    """Run ``squashmigrations`` and validate the generated replacement migration."""

    migration_dir = repo_root / "apps" / app_label / "migrations"
    before_files = {path.name for path in migration_dir.glob("*.py")}

    result = _run_manage(
        "squashmigrations",
        app_label,
        start,
        target,
        "--noinput",
        repo_root=repo_root,
    )
    if result.returncode != 0:
        output = "\n".join(part for part in (result.stdout, result.stderr) if part)
        raise MigrationBaselineError(
            f"squashmigrations failed for {app_label}:{start}->{target}\n{output.strip()}"
        )

    after_files = {path.name for path in migration_dir.glob("*.py")}
    new_files = sorted(after_files - before_files)
    if not new_files:
        raise MigrationBaselineError(
            f"squashmigrations reported success but created no file for {app_label}:{start}->{target}"
        )

    generated = migration_dir / new_files[-1]
    try:
        _validate_squashed_file(app_label, generated)
    except MigrationBaselineError:
        generated.unlink(missing_ok=True)
        raise
    return generated


def _parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse command-line arguments for migration baseline generation."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apps", nargs="+", default=list(DEFAULT_APPS))
    parser.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD)
    parser.add_argument("--recent-window", type=int, default=DEFAULT_RECENT_SQUASH_WINDOW)
    parser.add_argument("--execute", action="store_true", help="Run squashmigrations.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point for migration baseline generation."""

    args = _parse_args(argv or sys.argv[1:])
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    os.environ.setdefault("ARTHEXIS_DB_BACKEND", "sqlite")

    statuses = [evaluate_app_baseline(app_label) for app_label in args.apps]
    targets = [
        status
        for status in statuses
        if status.exceeds_threshold(args.threshold)
        and not status.has_recent_squash(args.recent_window)
        and status.squash_target
        and status.squash_start
    ]

    if not targets:
        print("No migration baseline actions required.")
        return 0

    for status in targets:
        print(
            f"App {status.app_label}: depth={status.active_chain_depth}, "
            f"latest={status.latest_number}, last_squash={status.latest_squash_number}"
        )

    if not args.execute:
        print("Dry run complete. Re-run with --execute to generate squash migrations.")
        return 0

    try:
        for status in targets:
            assert status.squash_target is not None
            assert status.squash_start is not None
            generated = squash_app_to_target(
                status.app_label,
                status.squash_start,
                status.squash_target,
            )
            print(f"Generated {generated.relative_to(REPO_ROOT)}")
    except MigrationBaselineError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
