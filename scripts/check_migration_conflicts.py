#!/usr/bin/env python3
"""Detect migration graph conflicts before running Django migration checks in CI."""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATION_NAME_RE = re.compile(r"^(?P<number>\d{4})_(?P<slug>[a-z0-9_]+)$")
MIGRATION_SUFFIX_RE = re.compile(r"(?:^|_)(?:t\d+|pr\d+)$")


@dataclass(frozen=True)
class MigrationIssue:
    """Represents a single migration policy failure."""

    app_label: str
    message: str
    files: tuple[str, ...]


@dataclass
class MigrationInfo:
    """Tracks migration file names and same-app dependencies."""

    app_label: str
    file_path: Path
    name: str
    dependencies: set[str]


def _migration_files(app_labels: set[str] | None = None) -> list[Path]:
    """Return migration files under apps/*/migrations excluding __init__.py."""

    files = [
        path
        for path in (REPO_ROOT / "apps").glob("*/migrations/*.py")
        if path.name != "__init__.py"
    ]
    if app_labels:
        files = [path for path in files if _app_label_for_path(path) in app_labels]
    return sorted(files)


def _app_label_for_path(path: Path) -> str:
    """Extract app label from apps/<app>/migrations/<file>.py paths."""

    return path.parts[-3]


def _parse_dependencies(path: Path, app_label: str) -> set[str]:
    """Parse same-app migration dependencies from a migration file."""

    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except OSError as exc:
        raise RuntimeError(f"Unable to read migration file {path}: {exc}") from exc
    except SyntaxError as exc:
        raise RuntimeError(f"Unable to parse migration file {path}: {exc}") from exc

    dependencies: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != "Migration":
            continue
        for class_item in node.body:
            if not isinstance(class_item, ast.Assign):
                continue
            for target in class_item.targets:
                if isinstance(target, ast.Name) and target.id == "dependencies":
                    if not isinstance(class_item.value, (ast.List, ast.Tuple)):
                        continue
                    for dep in class_item.value.elts:
                        if not isinstance(dep, ast.Tuple) or len(dep.elts) != 2:
                            continue
                        app_node, migration_node = dep.elts
                        if not (
                            isinstance(app_node, ast.Constant)
                            and isinstance(app_node.value, str)
                            and isinstance(migration_node, ast.Constant)
                            and isinstance(migration_node.value, str)
                        ):
                            continue
                        if app_node.value == app_label:
                            dependencies.add(migration_node.value)
    return dependencies


def _load_migration_infos(
    app_labels: set[str] | None = None,
) -> dict[str, dict[str, MigrationInfo]]:
    """Load migration metadata grouped by app label then migration name."""

    infos: dict[str, dict[str, MigrationInfo]] = {}
    for file_path in _migration_files(app_labels):
        app_label = _app_label_for_path(file_path)
        migration_name = file_path.stem
        app_infos = infos.setdefault(app_label, {})
        app_infos[migration_name] = MigrationInfo(
            app_label=app_label,
            file_path=file_path,
            name=migration_name,
            dependencies=_parse_dependencies(file_path, app_label),
        )
    return infos


def _find_leaf_conflicts(infos: dict[str, dict[str, MigrationInfo]]) -> list[MigrationIssue]:
    """Return issues for apps with duplicate leaves in the same migration graph."""

    issues: list[MigrationIssue] = []
    for app_label, app_infos in infos.items():
        children: dict[str, set[str]] = {name: set() for name in app_infos}
        for migration in app_infos.values():
            for dependency in migration.dependencies:
                if dependency in children:
                    children[dependency].add(migration.name)

        leaves = sorted(name for name, dependents in children.items() if not dependents)
        if len(leaves) <= 1:
            continue

        leaf_files = tuple(
            str(app_infos[leaf].file_path.relative_to(REPO_ROOT)) for leaf in leaves
        )
        issues.append(
            MigrationIssue(
                app_label=app_label,
                message="multiple leaf migrations detected",
                files=leaf_files,
            )
        )
    return issues


def _find_parallel_merge_chains(
    infos: dict[str, dict[str, MigrationInfo]],
) -> list[MigrationIssue]:
    """Return issues for merge migrations that merge same-number branches."""

    issues: list[MigrationIssue] = []
    for app_label, app_infos in infos.items():
        for migration in app_infos.values():
            if "_merge_" not in migration.name:
                continue
            local_parents = sorted(
                parent for parent in migration.dependencies if parent in app_infos
            )
            if len(local_parents) < 2:
                continue

            prefix_groups: dict[str, list[str]] = {}
            for parent in local_parents:
                match = MIGRATION_NAME_RE.match(parent)
                prefix = match.group("number") if match else "unknown"
                prefix_groups.setdefault(prefix, []).append(parent)

            conflicting_groups = [group for group in prefix_groups.values() if len(group) > 1]
            if not conflicting_groups:
                continue

            files = [str(migration.file_path.relative_to(REPO_ROOT))]
            for group in conflicting_groups:
                files.extend(
                    str(app_infos[parent].file_path.relative_to(REPO_ROOT))
                    for parent in sorted(group)
                )

            issues.append(
                MigrationIssue(
                    app_label=app_label,
                    message=(
                        "merge migration depends on parallel branches with the same "
                        "migration number"
                    ),
                    files=tuple(dict.fromkeys(files)),
                )
            )
    return issues


def _resolve_base_ref(base_ref: str | None) -> str | None:
    """Resolve a base ref for naming checks, returning None when unavailable."""

    if base_ref:
        return base_ref

    for candidate in ("origin/main", "origin/master"):
        probe = subprocess.run(
            ["git", "rev-parse", "--verify", candidate],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
        )
        if probe.returncode == 0:
            return candidate
    return None


def _changed_migration_files(base_ref: str | None, *, filter_codes: str) -> list[Path]:
    """Return migration files changed compared with base_ref."""

    if not base_ref:
        return []

    diff = subprocess.run(
        [
            "git",
            "diff",
            "--name-status",
            f"--diff-filter={filter_codes}",
            f"{base_ref}...HEAD",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    if diff.returncode != 0:
        return []

    changed_paths: list[Path] = []
    for line in diff.stdout.splitlines():
        parts = line.split("\t")
        if not parts:
            continue
        status = parts[0]
        path_field = parts[-1]
        if status.startswith("R") and len(parts) >= 3:
            path_field = parts[2]
        if not path_field.startswith("apps/") or "/migrations/" not in path_field:
            continue
        if not path_field.endswith(".py") or path_field.endswith("/__init__.py"):
            continue
        changed_paths.append(REPO_ROOT / path_field)
    return changed_paths


def _find_naming_issues(changed_files: Iterable[Path]) -> list[MigrationIssue]:
    """Return naming issues for newly-added migration files in the branch."""

    issues: list[MigrationIssue] = []
    for file_path in changed_files:
        app_label = _app_label_for_path(file_path)
        stem = file_path.stem
        if stem.endswith("_initial") or "_merge_" in stem or "_squashed_" in stem:
            continue

        match = MIGRATION_NAME_RE.match(stem)
        if not match:
            issues.append(
                MigrationIssue(
                    app_label=app_label,
                    message=(
                        "migration file does not match '<number>_<slug>_<ticket>' format"
                    ),
                    files=(str(file_path.relative_to(REPO_ROOT)),),
                )
            )
            continue

        slug = match.group("slug")
        if not MIGRATION_SUFFIX_RE.search(slug):
            issues.append(
                MigrationIssue(
                    app_label=app_label,
                    message=(
                        "migration slug must end with ticket/PR suffix like '_t1234' "
                        "or '_pr5678'"
                    ),
                    files=(str(file_path.relative_to(REPO_ROOT)),),
                )
            )
    return issues


def _print_failure(issues: Iterable[MigrationIssue]) -> None:
    """Print actionable, app-specific migration conflict details."""

    issue_list = list(issues)
    if not issue_list:
        return

    print(
        "Migration policy check failed before Django migration checks. "
        "Resolve the app-level conflicts listed below:",
        file=sys.stderr,
    )
    for issue in issue_list:
        print(f"- app='{issue.app_label}': {issue.message}", file=sys.stderr)
        for file_path in issue.files:
            print(f"    * {file_path}", file=sys.stderr)
    print(
        "Hint: create or adjust a merge migration for duplicate leaves and rename "
        "new migrations to include a ticket/PR suffix (e.g. 0042_add_index_pr1234.py).",
        file=sys.stderr,
    )


def main(argv: list[str] | None = None) -> int:
    """Run migration conflict and naming checks for CI."""

    args = argv or sys.argv[1:]
    base_ref_arg = args[0] if args else None
    resolved_base_ref = _resolve_base_ref(base_ref_arg)

    changed_for_graph = _changed_migration_files(resolved_base_ref, filter_codes="AMR")
    app_labels = {_app_label_for_path(path) for path in changed_for_graph}
    infos = _load_migration_infos(app_labels if app_labels else None)

    issues: list[MigrationIssue] = []
    if app_labels:
        issues.extend(_find_leaf_conflicts(infos))
        issues.extend(_find_parallel_merge_chains(infos))

    changed_added_or_renamed = _changed_migration_files(
        resolved_base_ref, filter_codes="AR"
    )
    issues.extend(_find_naming_issues(changed_added_or_renamed))

    if issues:
        _print_failure(issues)
        return 1

    print("Migration conflict pre-check passed.")
    return 0


if __name__ == "__main__":  # pragma: no cover - script entry
    raise SystemExit(main())
