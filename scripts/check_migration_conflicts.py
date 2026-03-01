#!/usr/bin/env python3
"""Detect migration graph conflicts before Django migration checks run.

This script scans local app migration files and fails fast when it detects:

* Duplicate leaf migrations in an app's graph (a common parallel-branch conflict).
* Suspicious parallel merge chains (multiple merge migrations or merge-on-merge chains).
* Migration filenames that do not include a ticket/PR suffix.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))
APPS_DIR = REPO_ROOT / "apps"

MIGRATION_FILE_PATTERN = re.compile(r"^(?P<number>\d{4})_(?P<name>[a-z0-9_]+)\.py$")
MERGE_NAME_PATTERN = re.compile(r"(^|_)merge(_|$)")
NAMES_WITHOUT_SUFFIX = {"initial"}
TICKET_SUFFIX_PATTERN = re.compile(r"(?:^|_)(?:t|ticket|pr)_?\d+$")


@dataclass(frozen=True, slots=True)
class MigrationFile:
    """Metadata parsed from a migration file path."""

    app_label: str
    name: str
    number: int
    path: Path


class MigrationParseError(ValueError):
    """Raised when a migration file cannot be parsed safely."""


class MigrationCheckError(RuntimeError):
    """Raised when migration checks detect policy violations."""


def _parse_assignment_tuples(path: Path, attribute_name: str) -> list[tuple[str, str]]:
    """Return literal ``Migration.<attribute_name>`` tuple values from ``path``."""

    try:
        module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except OSError as exc:
        raise MigrationParseError(f"Unable to read migration file {path}.") from exc
    except SyntaxError as exc:
        raise MigrationParseError(f"Unable to parse migration file {path}: {exc.msg}") from exc

    for node in module.body:
        if not isinstance(node, ast.ClassDef) or node.name != "Migration":
            continue
        for statement in node.body:
            if not isinstance(statement, ast.Assign):
                continue
            for target in statement.targets:
                if not isinstance(target, ast.Name) or target.id != attribute_name:
                    continue
                dependencies: list[tuple[str, str]] = []
                if isinstance(statement.value, ast.List):
                    for element in statement.value.elts:
                        if not isinstance(element, ast.Tuple) or len(element.elts) != 2:
                            continue
                        app_node, name_node = element.elts
                        if isinstance(app_node, ast.Constant) and isinstance(name_node, ast.Constant):
                            if isinstance(app_node.value, str) and isinstance(name_node.value, str):
                                dependencies.append((app_node.value, name_node.value))
                return dependencies
    return []


def _parse_dependencies(path: Path) -> list[tuple[str, str]]:
    """Return literal ``Migration.dependencies`` values from ``path``."""

    return _parse_assignment_tuples(path, "dependencies")


def _parse_replaces(path: Path) -> list[tuple[str, str]]:
    """Return literal ``Migration.replaces`` values from ``path``."""

    return _parse_assignment_tuples(path, "replaces")


def _migration_files_for_app(app_dir: Path) -> list[MigrationFile]:
    """Collect migration files for ``app_dir`` sorted by number and name."""

    migrations_dir = app_dir / "migrations"
    if not migrations_dir.exists():
        return []

    files: list[MigrationFile] = []
    for path in migrations_dir.glob("*.py"):
        if path.name == "__init__.py":
            continue
        match = MIGRATION_FILE_PATTERN.match(path.name)
        if not match:
            continue
        files.append(
            MigrationFile(
                app_label=app_dir.name,
                name=path.stem,
                number=int(match.group("number")),
                path=path,
            )
        )
    return sorted(files, key=lambda item: (item.number, item.name))


def _leaf_migrations(files: list[MigrationFile], dependencies_by_name: dict[str, list[tuple[str, str]]]) -> list[MigrationFile]:
    """Return migrations that are not depended on by another local migration."""

    pointed_to: set[str] = set()
    app_label = files[0].app_label if files else ""
    for dependencies in dependencies_by_name.values():
        for dep_app, dep_name in dependencies:
            if dep_app == app_label:
                pointed_to.add(dep_name)
    return [migration for migration in files if migration.name not in pointed_to]


def _is_merge_migration(migration_name: str) -> bool:
    """Return whether ``migration_name`` looks like a merge migration."""

    suffix = migration_name.split("_", 1)[1] if "_" in migration_name else migration_name
    return bool(MERGE_NAME_PATTERN.search(suffix))


def _has_required_suffix(migration_name: str) -> bool:
    """Return whether ``migration_name`` follows the ticket/PR suffix policy."""

    suffix = migration_name.split("_", 1)[1] if "_" in migration_name else migration_name
    if suffix in NAMES_WITHOUT_SUFFIX:
        return True
    if _is_merge_migration(migration_name):
        return True
    if suffix.startswith("squashed_"):
        return True
    return bool(TICKET_SUFFIX_PATTERN.search(suffix))


def _check_app(files: list[MigrationFile], *, repo_root: Path = REPO_ROOT) -> list[str]:
    """Evaluate migration safety checks for one app and return failures."""

    if not files:
        return []

    dependencies_by_name = {
        migration.name: _parse_dependencies(migration.path)
        for migration in files
    }
    replaces_by_name = {
        migration.name: _parse_replaces(migration.path)
        for migration in files
    }
    replaced_names = {
        replace_name
        for replaces in replaces_by_name.values()
        for replace_app, replace_name in replaces
        if replace_app == files[0].app_label
    }

    active_files = [migration for migration in files if migration.name not in replaced_names]
    leaves = _leaf_migrations(active_files, dependencies_by_name)
    merge_files = [migration for migration in active_files if _is_merge_migration(migration.name)]

    errors: list[str] = []
    if len(leaves) > 1:
        leaf_paths = ", ".join(str(migration.path.relative_to(repo_root)) for migration in leaves)
        errors.append(
            "duplicate leaf migrations detected; resolve by creating/adjusting a merge migration "
            f"for app '{files[0].app_label}'. Leaves: {leaf_paths}"
        )

    merge_name_set = {migration.name for migration in merge_files}
    merge_chain = [
        migration
        for migration in merge_files
        if any(dep_app == files[0].app_label and dep_name in merge_name_set for dep_app, dep_name in dependencies_by_name[migration.name])
    ]
    if len(merge_files) > 1 and (len(merge_chain) > 0 or len([leaf for leaf in leaves if _is_merge_migration(leaf.name)]) > 1):
        merge_paths = ", ".join(str(migration.path.relative_to(repo_root)) for migration in merge_files)
        errors.append(
            "suspicious parallel merge chain detected; multiple merge migrations exist in "
            f"app '{files[0].app_label}'. Merge files: {merge_paths}"
        )

    migrations_by_number: dict[int, list[MigrationFile]] = {}
    for migration in files:
        migrations_by_number.setdefault(migration.number, []).append(migration)

    duplicate_number_files = [
        migration
        for number, grouped in migrations_by_number.items()
        if len(grouped) > 1
        for migration in grouped
    ]
    invalid_names = [
        migration
        for migration in duplicate_number_files
        if not _has_required_suffix(migration.name)
    ]
    if invalid_names:
        invalid_paths = ", ".join(str(migration.path.relative_to(repo_root)) for migration in invalid_names)
        errors.append(
            "migration naming policy violation in "
            f"app '{files[0].app_label}'; duplicate migration numbers must include a ticket/PR suffix "
            "(for example: 0007_add_widget_pr1234.py). Invalid files: "
            f"{invalid_paths}"
        )

    return errors




def _git_changed_app_labels(repo_root: Path) -> set[str]:
    """Return app labels that have migration-file changes in the current branch."""

    def _run_git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=repo_root,
            text=True,
            capture_output=True,
        )

    base_ref_candidates = ["origin/HEAD", "origin/main", "origin/master", "HEAD~1"]
    diff_base: str | None = None
    for candidate in base_ref_candidates:
        merge_base = _run_git("merge-base", "HEAD", candidate)
        if merge_base.returncode == 0 and merge_base.stdout.strip():
            diff_base = merge_base.stdout.strip()
            break

    if diff_base is None:
        return set()

    diff = _run_git(
        "diff",
        "--name-only",
        "--diff-filter=ACMR",
        f"{diff_base}...HEAD",
        "--",
        "apps/*/migrations/*.py",
    )
    if diff.returncode != 0:
        return set()

    labels: set[str] = set()
    for line in diff.stdout.splitlines():
        parts = Path(line).parts
        if len(parts) >= 4 and parts[0] == "apps" and parts[2] == "migrations":
            labels.add(parts[1])
    return labels

def _local_installed_app_labels(repo_root: Path) -> list[str]:
    """Return installed local app labels when Django settings are available."""

    try:
        import os
        import django
        from django.apps import apps
        from django.conf import settings
    except ModuleNotFoundError:
        return []

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    os.environ.setdefault("ARTHEXIS_DB_BACKEND", "sqlite")
    django.setup()

    base_dir = Path(settings.BASE_DIR)
    labels: list[str] = []
    for app_config in apps.get_app_configs():
        try:
            Path(app_config.path).relative_to(base_dir)
        except ValueError:
            continue
        labels.append(app_config.label)
    return labels

def run_checks(repo_root: Path = REPO_ROOT, *, app_labels: set[str] | None = None) -> int:
    """Run migration conflict checks and return a process exit code."""

    all_errors: list[str] = []
    if app_labels is None:
        changed_labels = _git_changed_app_labels(repo_root)
        if not changed_labels:
            print("Migration conflict pre-check skipped: no changed migration files detected.")
            return 0

        installed_labels = set(_local_installed_app_labels(repo_root))
        target_labels = changed_labels & installed_labels if installed_labels else changed_labels
    else:
        target_labels = set(app_labels)

    for app_dir in sorted((repo_root / "apps").iterdir()):
        if not app_dir.is_dir():
            continue
        if app_dir.name not in target_labels:
            continue
        app_files = _migration_files_for_app(app_dir)
        all_errors.extend(_check_app(app_files, repo_root=repo_root))

    if all_errors:
        print("Migration conflict pre-check failed:", file=sys.stderr)
        for error in all_errors:
            print(f"  - {error}", file=sys.stderr)
        print(
            "Action: fix the listed app migration files (rename with ticket/PR suffix, "
            "or add/repair merge migrations) before running Django migration checks.",
            file=sys.stderr,
        )
        return 1

    print("Migration conflict pre-check passed.")
    return 0


def main() -> int:
    """Script entrypoint."""

    try:
        return run_checks()
    except (MigrationParseError, OSError) as exc:
        raise MigrationCheckError(f"Migration conflict pre-check failed: {exc}") from exc


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except MigrationCheckError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
