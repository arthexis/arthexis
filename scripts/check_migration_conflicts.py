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
    migration_label: str
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
                if not isinstance(statement.value, ast.List):
                    raise MigrationParseError(
                        f"{path}: Migration.{attribute_name} must be a literal list of 2-item tuples."
                    )
                parsed: list[tuple[str, str]] = []
                for element in statement.value.elts:
                    if not isinstance(element, ast.Tuple) or len(element.elts) != 2:
                        raise MigrationParseError(
                            f"{path}: Migration.{attribute_name} has invalid entry {ast.dump(element)}; "
                            "expected a 2-item tuple of string literals."
                        )
                    app_node, name_node = element.elts
                    if not (
                        isinstance(app_node, ast.Constant)
                        and isinstance(app_node.value, str)
                        and isinstance(name_node, ast.Constant)
                        and isinstance(name_node.value, str)
                    ):
                        raise MigrationParseError(
                            f"{path}: Migration.{attribute_name} has non-literal entry {ast.dump(element)}; "
                            "expected (str, str)."
                        )
                    parsed.append((app_node.value, name_node.value))
                return parsed
    return []


def _parse_dependencies(path: Path) -> list[tuple[str, str]]:
    """Return literal ``Migration.dependencies`` values from ``path``."""

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
                if not isinstance(target, ast.Name) or target.id != "dependencies":
                    continue
                if not isinstance(statement.value, ast.List):
                    raise MigrationParseError(
                        f"{path}: Migration.dependencies must be a literal list of dependencies."
                    )

                parsed: list[tuple[str, str]] = []
                for element in statement.value.elts:
                    if isinstance(element, ast.Tuple) and len(element.elts) == 2:
                        app_node, name_node = element.elts
                        if not (
                            isinstance(app_node, ast.Constant)
                            and isinstance(app_node.value, str)
                            and isinstance(name_node, ast.Constant)
                            and isinstance(name_node.value, str)
                        ):
                            raise MigrationParseError(
                                f"{path}: Migration.dependencies has non-literal entry {ast.dump(element)}; "
                                "expected (str, str)."
                            )
                        parsed.append((app_node.value, name_node.value))
                        continue

                    if _is_swappable_dependency(element):
                        # Django-generated migrations often include
                        # migrations.swappable_dependency(settings.AUTH_USER_MODEL).
                        # It is dynamic by design and never points to a local
                        # app migration node by name, so we skip it for static
                        # graph checks.
                        continue

                    raise MigrationParseError(
                        f"{path}: Migration.dependencies has invalid entry {ast.dump(element)}; "
                        "expected a 2-item tuple of string literals or "
                        "migrations.swappable_dependency(settings.AUTH_USER_MODEL)."
                    )
                return parsed

    return []


def _is_swappable_dependency(node: ast.expr) -> bool:
    """Return whether ``node`` is ``migrations.swappable_dependency(settings.AUTH_USER_MODEL)``."""

    if not isinstance(node, ast.Call) or len(node.args) != 1 or node.keywords:
        return False

    if not (
        isinstance(node.func, ast.Attribute)
        and node.func.attr == "swappable_dependency"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "migrations"
    ):
        return False

    arg = node.args[0]
    return (
        isinstance(arg, ast.Attribute)
        and arg.attr == "AUTH_USER_MODEL"
        and isinstance(arg.value, ast.Name)
        and arg.value.id == "settings"
    )


def _parse_replaces(path: Path) -> list[tuple[str, str]]:
    """Return literal ``Migration.replaces`` values from ``path``."""

    return _parse_assignment_tuples(path, "replaces")


def _app_migration_label(app_dir: Path) -> str:
    """Return the Django migration label for ``app_dir``.

    Most local app directories use their directory name as the Django app label.
    ``apps.sites`` is an intentional exception: its runtime package is
    ``apps.sites`` while its historical migration/model label remains ``pages``.
    The static conflict check must follow dependencies by Django label, not by
    filesystem directory name.
    """

    apps_py = app_dir / "apps.py"
    if not apps_py.exists():
        return app_dir.name

    try:
        module = ast.parse(apps_py.read_text(encoding="utf-8"), filename=str(apps_py))
    except (OSError, SyntaxError):
        return app_dir.name

    expected_name = f"apps.{app_dir.name}"
    fallback_label: str | None = None
    for node in module.body:
        if not isinstance(node, ast.ClassDef):
            continue

        config_name: str | None = None
        config_label: str | None = None
        for statement in node.body:
            if not isinstance(statement, ast.Assign):
                continue
            for target in statement.targets:
                if not isinstance(target, ast.Name):
                    continue
                if (
                    target.id == "name"
                    and isinstance(statement.value, ast.Constant)
                    and isinstance(statement.value.value, str)
                ):
                    config_name = statement.value.value
                if (
                    target.id == "label"
                    and isinstance(statement.value, ast.Constant)
                    and isinstance(statement.value.value, str)
                ):
                    config_label = statement.value.value

        if config_name == expected_name:
            return config_label or app_dir.name
        if config_name is None and config_label is not None:
            fallback_label = config_label

    return fallback_label or app_dir.name


def _migration_files_for_app(app_dir: Path) -> list[MigrationFile]:
    """Collect migration files for ``app_dir`` sorted by number and name."""

    migrations_dir = app_dir / "migrations"
    if not migrations_dir.exists():
        return []

    migration_label = _app_migration_label(app_dir)
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
                migration_label=migration_label,
                name=path.stem,
                number=int(match.group("number")),
                path=path,
            )
        )
    return sorted(files, key=lambda item: (item.number, item.name))


def _leaf_migrations(files: list[MigrationFile], dependencies_by_name: dict[str, list[tuple[str, str]]]) -> list[MigrationFile]:
    """Return migrations that are not depended on by another local migration."""

    pointed_to: set[str] = set()
    app_label = files[0].migration_label if files else ""
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
        if replace_app == files[0].migration_label
    }

    active_files = [migration for migration in files if migration.name not in replaced_names]
    leaves = _leaf_migrations(active_files, dependencies_by_name)
    merge_files = [migration for migration in active_files if _is_merge_migration(migration.name)]

    errors: list[str] = []
    if len(leaves) > 1:
        leaf_paths = ", ".join(migration.path.relative_to(repo_root).as_posix() for migration in leaves)
        errors.append(
            "duplicate leaf migrations detected; resolve by creating/adjusting a merge migration "
            f"for app '{files[0].app_label}'. Leaves: {leaf_paths}"
        )

    merge_name_set = {migration.name for migration in merge_files}
    merge_chain = [
        migration
        for migration in merge_files
        if any(dep_app == files[0].migration_label and dep_name in merge_name_set for dep_app, dep_name in dependencies_by_name[migration.name])
    ]
    if len(merge_files) > 1 and (len(merge_chain) > 0 or len([leaf for leaf in leaves if _is_merge_migration(leaf.name)]) > 1):
        merge_paths = ", ".join(
            migration.path.relative_to(repo_root).as_posix() for migration in merge_files
        )
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
        invalid_paths = ", ".join(
            migration.path.relative_to(repo_root).as_posix() for migration in invalid_names
        )
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

    def _labels_from_diff_paths(paths: str) -> set[str]:
        labels: set[str] = set()
        for line in paths.splitlines():
            parts = Path(line).parts
            if len(parts) >= 4 and parts[0] == "apps" and parts[2] == "migrations":
                labels.add(parts[1])
        return labels

    base_ref_candidates = ["origin/HEAD", "origin/main", "origin/master", "HEAD~1"]
    diff_base: str | None = None
    for candidate in base_ref_candidates:
        merge_base = _run_git("merge-base", "HEAD", candidate)
        if merge_base.returncode == 0 and merge_base.stdout.strip():
            diff_base = merge_base.stdout.strip()
            break

    if diff_base is None:
        # Some CI checkouts (notably staged upgrade jobs) do not keep enough git
        # history/refs to resolve a merge-base. In that case, first diff against
        # HEAD's first parent. GitHub Actions PR merge commits have parent[0]
        # pointing at the base branch, so this limits checks to PR-introduced
        # migration changes instead of aggregating both parents.
        head_diff = _run_git(
            "diff",
            "--name-only",
            "--diff-filter=ACMR",
            "HEAD^1..HEAD",
            "--",
            "apps/*/migrations/*.py",
        )
        if head_diff.returncode == 0:
            return _labels_from_diff_paths(head_diff.stdout)

        # For non-merge commits (or very shallow clones where HEAD^1 is missing),
        # try commit-level file discovery as a second narrow fallback.
        head_diff = _run_git(
            "diff-tree",
            "--no-commit-id",
            "--name-only",
            "--diff-filter=ACMR",
            "-r",
            "HEAD",
            "--",
            "apps/*/migrations/*.py",
        )
        if head_diff.returncode == 0:
            return _labels_from_diff_paths(head_diff.stdout)

        # If commit-level diff commands are unavailable, fail open by scanning all local
        # app migration directories rather than failing the entire validation job.
        labels: set[str] = set()
        apps_dir = repo_root / "apps"
        if not apps_dir.exists() or not apps_dir.is_dir():
            return labels
        for app_dir in apps_dir.iterdir():
            if not app_dir.is_dir():
                continue
            migrations_dir = app_dir / "migrations"
            if migrations_dir.exists() and any(migrations_dir.glob("[0-9][0-9][0-9][0-9]_*.py")):
                labels.add(app_dir.name)
        return labels

    diff = _run_git(
        "diff",
        "--name-only",
        "--diff-filter=ACMR",
        f"{diff_base}...HEAD",
        "--",
        "apps/*/migrations/*.py",
    )
    if diff.returncode != 0:
        stderr = diff.stderr.strip()
        raise MigrationCheckError(
            "git diff failed while discovering changed migration files: "
            f"{stderr or 'unknown error'}"
        )

    return _labels_from_diff_paths(diff.stdout)

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
        labels.append(Path(app_config.path).name)
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
