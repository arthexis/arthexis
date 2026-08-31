"""Helpers for reading local Django app manifest metadata."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from functools import cache
from pathlib import Path


@dataclass(frozen=True)
class AppManifest:
    """Parsed app manifest metadata."""

    path: Path
    django_apps: tuple[str, ...]
    optional_django_apps: tuple[str, ...]
    requires_apps: tuple[str, ...]


def _default_base_dir() -> Path:
    return Path(__file__).resolve().parents[1]


def _assignment_value(tree: ast.Module, name: str) -> ast.AST | None:
    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            for target in statement.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return statement.value
        elif (
            isinstance(statement, ast.AnnAssign)
            and isinstance(statement.target, ast.Name)
            and statement.target.id == name
        ):
            return statement.value
    return None


def _literal_string_list(
    value: ast.AST | None,
    *,
    field_name: str,
    path: Path,
) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, ast.List | ast.Tuple):
        raise ValueError(f"{path}: {field_name} must be a list of strings.")

    entries: list[str] = []
    seen: set[str] = set()
    for element in value.elts:
        if not isinstance(element, ast.Constant) or not isinstance(element.value, str):
            raise ValueError(f"{path}: {field_name} must be a list of strings.")
        entry = element.value.strip()
        if not entry or entry in seen:
            continue
        entries.append(entry)
        seen.add(entry)
    return tuple(entries)


def _parse_manifest(path: Path) -> AppManifest:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError) as exc:
        raise ValueError(f"{path}: manifest could not be parsed.") from exc

    return AppManifest(
        path=path,
        django_apps=_literal_string_list(
            _assignment_value(tree, "DJANGO_APPS"),
            field_name="DJANGO_APPS",
            path=path,
        ),
        optional_django_apps=_literal_string_list(
            _assignment_value(tree, "OPTIONAL_DJANGO_APPS"),
            field_name="OPTIONAL_DJANGO_APPS",
            path=path,
        ),
        requires_apps=_literal_string_list(
            _assignment_value(tree, "REQUIRES_APPS"),
            field_name="REQUIRES_APPS",
            path=path,
        ),
    )


def load_app_manifests(base_dir: str | Path | None = None) -> tuple[AppManifest, ...]:
    """Return parsed local app manifests from ``apps/**/manifest.py``."""

    resolved_base_dir = Path(base_dir) if base_dir is not None else _default_base_dir()
    return _load_app_manifests(str(resolved_base_dir.resolve()))


@cache
def _load_app_manifests(base_dir: str) -> tuple[AppManifest, ...]:
    apps_dir = Path(base_dir) / "apps"
    if not apps_dir.exists():
        return ()
    manifests = tuple(
        _parse_manifest(path)
        for path in sorted(apps_dir.rglob("manifest.py"))
        if path.is_file()
    )
    owners: dict[str, Path] = {}
    for manifest in manifests:
        for app_entry in (*manifest.django_apps, *manifest.optional_django_apps):
            previous_path = owners.setdefault(app_entry, manifest.path)
            if previous_path != manifest.path:
                raise ValueError(
                    f"{manifest.path}: {app_entry} is already declared in "
                    f"{previous_path}."
                )
    return manifests


def load_manifest_app_entries(base_dir: str | Path | None = None) -> set[str]:
    """Return every Django app entry declared by local manifests."""

    return {
        app_entry
        for manifest in load_app_manifests(base_dir)
        for app_entry in manifest.django_apps
    }


def load_manifest_declared_app_entries(base_dir: str | Path | None = None) -> set[str]:
    """Return app entries declared by manifests, including optional selectors."""

    return {
        app_entry
        for manifest in load_app_manifests(base_dir)
        for app_entry in (*manifest.django_apps, *manifest.optional_django_apps)
    }


def load_app_dependency_metadata(
    base_dir: str | Path | None = None,
) -> dict[str, tuple[str, ...]]:
    """Return app dependency declarations from manifest ``REQUIRES_APPS`` lists."""

    dependencies: dict[str, tuple[str, ...]] = {}
    for manifest in load_app_manifests(base_dir):
        if not manifest.requires_apps:
            continue
        for app_entry in (*manifest.django_apps, *manifest.optional_django_apps):
            dependencies[app_entry] = manifest.requires_apps
    return dependencies
