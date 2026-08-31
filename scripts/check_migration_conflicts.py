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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))
APPS_DIR = REPO_ROOT / "apps"

MIGRATION_FILE_PATTERN = re.compile(r"^(?P<number>\d{4})_(?P<name>[a-z0-9_]+)\.py$")
MERGE_NAME_PATTERN = re.compile(r"(^|_)merge(_|$)")
NAMES_WITHOUT_SUFFIX = {"initial"}
TICKET_SUFFIX_PATTERN = re.compile(r"(?:^|_)(?:t|ticket|pr)_?\d+$")
RUNPYTHON_RATIONALE_MARKERS = (
    "migration-lint: allow-runpython",
    "migration-lint: irreversible-ok",
    "migration-lint: rationale",
)
SEED_DATA_MARKERS = ("fixture", "loaddata", "seed", "template")


@dataclass(frozen=True, slots=True)
class MigrationFile:
    """Metadata parsed from a migration file path."""

    app_label: str
    name: str
    number: int
    path: Path


@dataclass(frozen=True, slots=True)
class MigrationLintFinding:
    """Static migration lint finding with stable machine-readable fields."""

    code: str
    line: int | None
    message: str
    path: Path
    severity: str = "error"

    def as_payload(self, *, repo_root: Path = REPO_ROOT) -> dict[str, Any]:
        try:
            display_path = self.path.relative_to(repo_root).as_posix()
        except ValueError:
            display_path = self.path.as_posix()
        return {
            "code": self.code,
            "line": self.line,
            "message": self.message,
            "path": display_path,
            "severity": self.severity,
        }


class MigrationParseError(ValueError):
    """Raised when a migration file cannot be parsed safely."""


class MigrationCheckError(RuntimeError):
    """Raised when migration checks detect policy violations."""


def _parse_migration_module(path: Path) -> ast.Module:
    """Parse a migration file into an AST with consistent errors."""

    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except OSError as exc:
        raise MigrationParseError(f"Unable to read migration file {path}.") from exc
    except SyntaxError as exc:
        raise MigrationParseError(f"Unable to parse migration file {path}: {exc.msg}") from exc


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


def _migration_operation_nodes(path: Path) -> list[ast.expr]:
    """Return AST nodes listed in ``Migration.operations``."""

    module = _parse_migration_module(path)
    for node in module.body:
        if not isinstance(node, ast.ClassDef) or node.name != "Migration":
            continue
        for statement in node.body:
            if not isinstance(statement, ast.Assign):
                continue
            for target in statement.targets:
                if not isinstance(target, ast.Name) or target.id != "operations":
                    continue
                if not isinstance(statement.value, ast.List):
                    raise MigrationParseError(
                        f"{path}: Migration.operations must be a literal list."
                    )
                return list(statement.value.elts)
    return []


def _call_name(node: ast.AST) -> str | None:
    """Return the final call name for an AST call/function node."""

    if isinstance(node, ast.Call):
        return _call_name(node.func)
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _keyword_node(call: ast.Call, name: str) -> ast.AST | None:
    """Return keyword value ``name`` from ``call`` when present."""

    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _literal_bool(node: ast.AST | None) -> bool | None:
    """Return a literal boolean value when ``node`` is ``True`` or ``False``."""

    if isinstance(node, ast.Constant) and isinstance(node.value, bool):
        return node.value
    return None


def _literal_string(node: ast.AST | None) -> str | None:
    """Return a literal string value when available."""

    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _operation_payload(node: ast.expr) -> dict[str, Any]:
    """Summarize one migration operation node for impact reports."""

    operation_class = _call_name(node) or type(node).__name__
    payload: dict[str, Any] = {
        "class": operation_class,
        "line": getattr(node, "lineno", None),
    }
    if isinstance(node, ast.Call):
        model_name = _literal_string(_keyword_node(node, "model_name"))
        field_name = _literal_string(_keyword_node(node, "name"))
        if model_name:
            payload["model_name"] = model_name
        if field_name:
            payload["name"] = field_name
        if operation_class == "RunPython":
            payload["data_migration"] = True
            payload["reversible"] = _runpython_has_reverse(node)
        elif operation_class == "RunSQL":
            payload["data_migration"] = True
            payload["reversible"] = _runsql_has_reverse(node)
        else:
            payload["data_migration"] = False
    return payload


def _runpython_has_reverse(call: ast.Call) -> bool:
    """Return whether ``migrations.RunPython`` includes explicit reverse handling."""

    if len(call.args) >= 2:
        return True
    return _keyword_node(call, "reverse_code") is not None


def _runsql_has_reverse(call: ast.Call) -> bool:
    """Return whether ``migrations.RunSQL`` includes explicit reverse SQL."""

    if len(call.args) >= 2:
        return True
    return _keyword_node(call, "reverse_sql") is not None


def _line_has_lint_rationale(lines: list[str], line: int | None) -> bool:
    """Return whether nearby comments declare a migration-lint rationale."""

    if line is None:
        return False
    start = max(0, line - 4)
    end = min(len(lines), line + 1)
    window = "\n".join(lines[start:end]).lower()
    return any(marker in window for marker in RUNPYTHON_RATIONALE_MARKERS)


def _field_call_from_addfield(call: ast.Call) -> ast.Call | None:
    """Return the field constructor passed to a ``migrations.AddField`` call."""

    field_node = _keyword_node(call, "field")
    if field_node is None and len(call.args) >= 3:
        field_node = call.args[2]
    return field_node if isinstance(field_node, ast.Call) else None


def _field_preserves_existing_rows(field_call: ast.Call) -> bool:
    """Return whether a field constructor is statically safe for existing rows."""

    if _literal_bool(_keyword_node(field_call, "null")) is True:
        return True
    if _literal_bool(_keyword_node(field_call, "primary_key")) is True:
        return True
    if _keyword_node(field_call, "default") is not None:
        return True
    return _keyword_node(field_call, "db_default") is not None


def _module_imports_current_models(module: ast.Module) -> list[ast.AST]:
    """Return import nodes that pull project runtime models into a migration."""

    imports: list[ast.AST] = []
    for node in ast.walk(module):
        if isinstance(node, ast.ImportFrom):
            module_name = node.module or ""
            imports_models_module = (
                module_name == "models"
                and node.level > 0
                or module_name.startswith("apps.")
                and (module_name.endswith(".models") or ".models." in module_name)
            )
            if imports_models_module:
                imports.append(node)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("apps.") and (
                    alias.name.endswith(".models") or ".models." in alias.name
                ):
                    imports.append(node)
                    break
    return imports


def lint_migration_file(
    path: Path, *, repo_root: Path = REPO_ROOT
) -> list[MigrationLintFinding]:
    """Run static lint checks for one migration file."""

    module = _parse_migration_module(path)
    lines = path.read_text(encoding="utf-8").splitlines()
    findings: list[MigrationLintFinding] = []

    for node in _module_imports_current_models(module):
        findings.append(
            MigrationLintFinding(
                code="current-model-import",
                line=getattr(node, "lineno", None),
                message=(
                    "Migration imports project runtime models; use the historical "
                    "apps registry passed to RunPython instead."
                ),
                path=path,
            )
        )

    operation_nodes = _migration_operation_nodes(path)
    operation_classes = {_call_name(node) for node in operation_nodes}
    has_backfill_operation = bool({"RunPython", "RunSQL"} & operation_classes)

    for node in operation_nodes:
        if not isinstance(node, ast.Call):
            continue
        operation_class = _call_name(node)
        line = getattr(node, "lineno", None)
        if operation_class == "RunPython" and not _runpython_has_reverse(node):
            if not _line_has_lint_rationale(lines, line):
                findings.append(
                    MigrationLintFinding(
                        code="runpython-without-reverse",
                        line=line,
                        message=(
                            "RunPython operation has no reverse handler or nearby "
                            "migration-lint rationale."
                        ),
                        path=path,
                    )
                )
        if operation_class != "AddField":
            continue
        field_call = _field_call_from_addfield(node)
        if field_call is None or _field_preserves_existing_rows(field_call):
            continue
        if has_backfill_operation or _line_has_lint_rationale(lines, line):
            continue
        findings.append(
            MigrationLintFinding(
                code="non-null-addfield-without-default",
                line=line,
                message=(
                    "AddField appears to add a non-null field without default, "
                    "db_default, null=True, or detectable backfill evidence."
                ),
                path=path,
            )
        )

    return findings


def _configured_app_label(app_dir: Path) -> str:
    """Return the Django app label for ``app_dir`` when it is declared locally."""

    apps_py = app_dir / "apps.py"
    if not apps_py.exists():
        return app_dir.name

    try:
        module = ast.parse(apps_py.read_text(encoding="utf-8"), filename=str(apps_py))
    except (OSError, SyntaxError):
        return app_dir.name

    for node in module.body:
        if not isinstance(node, ast.ClassDef):
            continue
        label = None
        name = None
        for statement in node.body:
            if isinstance(statement, ast.Assign):
                if len(statement.targets) != 1 or not isinstance(statement.targets[0], ast.Name):
                    continue
                target_name = statement.targets[0].id
                value_node = statement.value
            elif isinstance(statement, ast.AnnAssign):
                if not isinstance(statement.target, ast.Name):
                    continue
                target_name = statement.target.id
                value_node = statement.value
            else:
                continue

            if value_node is None or not isinstance(value_node, ast.Constant) or not isinstance(value_node.value, str):
                continue
            if target_name == "label":
                label = value_node.value
            elif target_name == "name":
                name = value_node.value
        if name == f"apps.{app_dir.name}" and label:
            return label

    return app_dir.name


def _migration_files_for_app(app_dir: Path) -> list[MigrationFile]:
    """Collect migration files for ``app_dir`` sorted by number and name."""

    migrations_dir = app_dir / "migrations"
    if not migrations_dir.exists():
        return []

    app_label = _configured_app_label(app_dir)
    files: list[MigrationFile] = []
    for path in migrations_dir.glob("*.py"):
        if path.name == "__init__.py":
            continue
        match = MIGRATION_FILE_PATTERN.match(path.name)
        if not match:
            continue
        files.append(
            MigrationFile(
                app_label=app_label,
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
        leaf_paths = ", ".join(migration.path.relative_to(repo_root).as_posix() for migration in leaves)
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


def _run_git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run a git command from ``repo_root``."""

    return subprocess.run(
        ["git", *args],
        cwd=repo_root,
        text=True,
        capture_output=True,
    )


def _git_ref(repo_root: Path, ref: str) -> str:
    """Return a short git ref when available."""

    result = _run_git(repo_root, "rev-parse", "--short=12", ref)
    return result.stdout.strip() if result.returncode == 0 else ""


def _diff_base(repo_root: Path, base_ref: str | None) -> str | None:
    """Resolve the base commit used for changed-file discovery."""

    if base_ref:
        merge_base = _run_git(repo_root, "merge-base", "HEAD", base_ref)
        if merge_base.returncode != 0 or not merge_base.stdout.strip():
            stderr = merge_base.stderr.strip()
            raise MigrationCheckError(
                f"Unable to resolve migration impact base {base_ref!r}: "
                f"{stderr or 'merge-base not found'}"
            )
        return merge_base.stdout.strip()

    for candidate in ("origin/HEAD", "origin/main", "origin/master", "HEAD~1"):
        merge_base = _run_git(repo_root, "merge-base", "HEAD", candidate)
        if merge_base.returncode == 0 and merge_base.stdout.strip():
            return merge_base.stdout.strip()
    return None


def _paths_from_diff_output(paths: str) -> list[Path]:
    """Return normalized repository-relative paths from git output."""

    return [Path(line.strip()) for line in paths.splitlines() if line.strip()]


def _git_changed_paths(
    repo_root: Path,
    *,
    base_ref: str | None = None,
    pathspecs: tuple[str, ...] = (),
) -> list[Path]:
    """Return changed paths for ``base_ref...HEAD`` with shallow fallbacks."""

    diff_base = _diff_base(repo_root, base_ref)
    if diff_base is None:
        head_diff = _run_git(
            repo_root,
            "diff",
            "--name-only",
            "--diff-filter=ACMR",
            "HEAD^1..HEAD",
            "--",
            *pathspecs,
        )
        if head_diff.returncode == 0:
            return _paths_from_diff_output(head_diff.stdout)

        head_diff = _run_git(
            repo_root,
            "diff-tree",
            "--no-commit-id",
            "--name-only",
            "--diff-filter=ACMR",
            "-r",
            "HEAD",
            "--",
            *pathspecs,
        )
        if head_diff.returncode == 0:
            return _paths_from_diff_output(head_diff.stdout)
        return []

    diff = _run_git(
        repo_root,
        "diff",
        "--name-only",
        "--diff-filter=ACMR",
        f"{diff_base}...HEAD",
        "--",
        *pathspecs,
    )
    if diff.returncode != 0:
        stderr = diff.stderr.strip()
        raise MigrationCheckError(
            "git diff failed while discovering changed files: "
            f"{stderr or 'unknown error'}"
        )
    return _paths_from_diff_output(diff.stdout)


def _is_migration_path(path: Path) -> bool:
    """Return whether ``path`` is a local app migration file."""

    parts = path.parts
    return (
        len(parts) >= 4
        and parts[0] == "apps"
        and parts[2] == "migrations"
        and parts[-1] != "__init__.py"
        and bool(MIGRATION_FILE_PATTERN.match(parts[-1]))
    )


def _git_changed_migration_paths(
    repo_root: Path, *, base_ref: str | None = None
) -> list[Path]:
    """Return changed local app migration files."""

    return [
        path
        for path in _git_changed_paths(
            repo_root,
            base_ref=base_ref,
            pathspecs=("apps/*/migrations/*.py",),
        )
        if _is_migration_path(path)
    ]


def _migration_labels_from_paths(paths: list[Path]) -> set[str]:
    """Return app directory names from local migration paths."""

    return {path.parts[1] for path in paths if _is_migration_path(path)}


def _is_fixture_or_seed_path(path: Path) -> bool:
    """Return whether a changed path likely affects fixtures or seed data."""

    parts = path.parts
    if "fixtures" in parts:
        return True
    if path.suffix.lower() in {".json", ".yaml", ".yml"}:
        return any(part in {"fixtures", "seed", "seeds"} for part in parts)
    return False


def _migration_report_entry(path: Path, *, repo_root: Path) -> dict[str, Any]:
    """Build one migration-file entry for the impact report."""

    full_path = repo_root / path
    app_dir = repo_root / "apps" / path.parts[1]
    app_label = _configured_app_label(app_dir)
    dependencies = _parse_dependencies(full_path)
    operations = [
        _operation_payload(node) for node in _migration_operation_nodes(full_path)
    ]
    operation_classes = sorted(
        {operation["class"] for operation in operations if operation.get("class")}
    )
    text = full_path.read_text(encoding="utf-8").lower()
    data_operations = [
        operation for operation in operations if operation.get("data_migration")
    ]
    seed_data_impact = bool(data_operations) and any(
        marker in text for marker in SEED_DATA_MARKERS
    )
    cross_app_dependencies = [
        {"app_label": dep_app, "migration_name": dep_name}
        for dep_app, dep_name in dependencies
        if dep_app != app_label
    ]
    rollback_notes: list[str] = []
    for operation in data_operations:
        if not operation.get("reversible"):
            rollback_notes.append(
                f"{operation['class']} at line {operation.get('line')} has no reverse handler."
            )
    if any(
        operation["class"] in {"RemoveField", "DeleteModel"} for operation in operations
    ):
        rollback_notes.append("Destructive schema operation detected.")
    if not rollback_notes:
        rollback_notes.append("No static rollback blockers detected.")

    return {
        "app_label": app_label,
        "cross_app_dependencies": cross_app_dependencies,
        "data_migration": bool(data_operations),
        "migration_name": path.stem,
        "operation_classes": operation_classes,
        "operations": operations,
        "path": path.as_posix(),
        "rollback_notes": rollback_notes,
        "seed_data_impact": seed_data_impact,
    }


def build_migration_impact_report(
    repo_root: Path = REPO_ROOT, *, base_ref: str = "origin/main"
) -> dict[str, Any]:
    """Build a structured migration impact report for changed files."""

    migration_paths = _git_changed_migration_paths(repo_root, base_ref=base_ref)
    all_changed_paths = _git_changed_paths(repo_root, base_ref=base_ref)
    fixture_paths = [
        path.as_posix() for path in all_changed_paths if _is_fixture_or_seed_path(path)
    ]
    migration_entries = [
        _migration_report_entry(path, repo_root=repo_root) for path in migration_paths
    ]
    findings = [
        finding.as_payload(repo_root=repo_root)
        for path in migration_paths
        for finding in lint_migration_file(repo_root / path, repo_root=repo_root)
    ]
    operation_classes = sorted(
        {
            operation_class
            for entry in migration_entries
            for operation_class in entry["operation_classes"]
        }
    )
    risk_reasons: list[str] = []
    if findings:
        risk_reasons.append("static migration lint findings")
    if any(entry["data_migration"] for entry in migration_entries):
        risk_reasons.append("data migration operations")
    if any(entry["cross_app_dependencies"] for entry in migration_entries):
        risk_reasons.append("cross-app migration dependencies")
    if fixture_paths or any(entry["seed_data_impact"] for entry in migration_entries):
        risk_reasons.append("fixture or seed-data impact")
    if any(
        operation_class in {"DeleteModel", "RemoveField", "RunSQL"}
        for operation_class in operation_classes
    ):
        risk_reasons.append("destructive or raw SQL operation")

    if findings or "destructive or raw SQL operation" in risk_reasons:
        risk_level = "high"
    elif risk_reasons:
        risk_level = "medium"
    else:
        risk_level = "low"

    return {
        "base_ref": base_ref,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git": {
            "base": _git_ref(repo_root, base_ref),
            "head": _git_ref(repo_root, "HEAD"),
        },
        "migration_files": migration_entries,
        "operation_classes": operation_classes,
        "risk": {
            "level": risk_level,
            "reasons": risk_reasons,
        },
        "schema_version": 1,
        "static_lint": findings,
        "summary": {
            "changed_migration_count": len(migration_entries),
            "data_migration_count": sum(
                1 for entry in migration_entries if entry["data_migration"]
            ),
            "fixture_or_seed_files": fixture_paths,
        },
        "tool": "migration-impact",
    }


def format_migration_impact_markdown(report: dict[str, Any]) -> str:
    """Render a migration impact report as compact Markdown."""

    lines = [
        "# Migration Impact Report",
        "",
        f"- Base ref: `{report['base_ref']}`",
        f"- Head: `{report['git'].get('head') or 'unknown'}`",
        f"- Risk: **{report['risk']['level']}**",
        f"- Changed migrations: {report['summary']['changed_migration_count']}",
    ]
    if report["risk"]["reasons"]:
        lines.append(f"- Risk reasons: {', '.join(report['risk']['reasons'])}")
    if report["operation_classes"]:
        lines.append(f"- Operation classes: {', '.join(report['operation_classes'])}")
    fixture_files = report["summary"]["fixture_or_seed_files"]
    if fixture_files:
        lines.append(f"- Fixture or seed files: {', '.join(fixture_files)}")

    lines.extend(["", "## Changed Migration Files"])
    if not report["migration_files"]:
        lines.append("No migration file changes detected.")
    for entry in report["migration_files"]:
        operations = (
            ", ".join(f"`{name}`" for name in entry["operation_classes"])
            if entry["operation_classes"]
            else "none detected"
        )
        lines.extend(
            [
                f"### `{entry['path']}`",
                f"- App: `{entry['app_label']}`",
                f"- Operations: {operations}",
                f"- Data migration: {'yes' if entry['data_migration'] else 'no'}",
                f"- Seed/fixture impact: {'yes' if entry['seed_data_impact'] else 'no'}",
            ]
        )
        if entry["cross_app_dependencies"]:
            dependencies = ", ".join(
                f"`{dependency['app_label']}.{dependency['migration_name']}`"
                for dependency in entry["cross_app_dependencies"]
            )
            lines.append(f"- Cross-app dependencies: {dependencies}")
        else:
            lines.append("- Cross-app dependencies: none")
        lines.append("- Rollback notes: " + "; ".join(entry["rollback_notes"]))

    lines.extend(["", "## Static Migration Lint"])
    if not report["static_lint"]:
        lines.append("No static migration lint findings.")
    for finding in report["static_lint"]:
        line = f":{finding['line']}" if finding.get("line") else ""
        lines.append(
            f"- `{finding['path']}{line}` {finding['code']}: {finding['message']}"
        )

    return "\n".join(lines) + "\n"




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
    return labels

def run_checks(repo_root: Path = REPO_ROOT, *, app_labels: set[str] | None = None) -> int:
    """Run migration conflict checks and return a process exit code."""

    all_errors: list[str] = []
    changed_migration_paths: list[Path] = []
    if app_labels is None:
        changed_migration_paths = _git_changed_migration_paths(repo_root)
        changed_labels = _migration_labels_from_paths(changed_migration_paths)
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

    for path in changed_migration_paths:
        if path.parts[1] not in target_labels:
            continue
        for finding in lint_migration_file(repo_root / path, repo_root=repo_root):
            payload = finding.as_payload(repo_root=repo_root)
            line = f":{payload['line']}" if payload.get("line") else ""
            all_errors.append(
                "static migration lint "
                f"{payload['path']}{line}: {payload['code']}: {payload['message']}"
            )

    if all_errors:
        print("Migration conflict pre-check failed:", file=sys.stderr)
        for error in all_errors:
            print(f"  - {error}", file=sys.stderr)
        print(
            "Action: fix the listed app migration files (rename with ticket/PR suffix, "
            "add/repair merge migrations, or address static lint findings) before "
            "running Django migration checks.",
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
        raise SystemExit(1) from None
