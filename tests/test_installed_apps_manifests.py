"""Tests for manifest-based Django app discovery in settings."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from django.core.exceptions import ImproperlyConfigured

from config import settings

pytestmark = pytest.mark.critical


def _extract_manifest_apps(manifest_path: Path) -> list[str]:
    """Return DJANGO_APPS from a manifest file using static AST evaluation."""

    module_ast = ast.parse(manifest_path.read_text(encoding="utf-8"))
    for statement in module_ast.body:
        if not isinstance(statement, ast.Assign):
            continue

        if len(statement.targets) != 1 or not isinstance(statement.targets[0], ast.Name):
            continue

        if statement.targets[0].id != "DJANGO_APPS":
            continue

        try:
            parsed_value = ast.literal_eval(statement.value)
        except (ValueError, SyntaxError) as exc:
            raise AssertionError(
                f"{manifest_path} must declare DJANGO_APPS as a literal list of strings"
            ) from exc

        if not isinstance(parsed_value, list):
            raise AssertionError(f"{manifest_path} DJANGO_APPS must be a list")

        if not all(isinstance(entry, str) and entry.strip() for entry in parsed_value):
            raise AssertionError(
                f"{manifest_path} DJANGO_APPS must contain non-empty string entries"
            )

        return [entry.strip() for entry in parsed_value]

    raise AssertionError(f"{manifest_path} must declare DJANGO_APPS")


def _expected_local_apps_from_manifest_files() -> list[str]:
    """Build expected local apps from manifest files to avoid brittle hard-coded lists."""

    app_entries: list[str] = []
    manifests = sorted((settings.BASE_DIR / "apps").rglob("manifest.py"))
    for manifest_path in manifests:
        app_entries.extend(_extract_manifest_apps(manifest_path))
    return app_entries


def _manifest_entries_with_source() -> list[tuple[str, str]]:
    """Return normalized app entries paired with their manifest module path."""

    entries: list[tuple[str, str]] = []
    manifests = sorted((settings.BASE_DIR / "apps").rglob("manifest.py"))
    for manifest_path in manifests:
        module_name = ".".join(
            manifest_path.relative_to(settings.BASE_DIR).with_suffix("").parts
        )
        for app_entry in _extract_manifest_apps(manifest_path):
            entries.append((module_name, app_entry.strip()))

    return entries


def test_local_apps_manifest_loading_is_complete_and_deterministic() -> None:
    """Regression: manifest loading should reproduce the project app list deterministically."""

    expected_local_apps = _expected_local_apps_from_manifest_files()
    first_load = settings._load_local_apps_from_manifests()
    second_load = settings._load_local_apps_from_manifests()

    assert first_load == expected_local_apps
    assert first_load == second_load
    assert len(first_load) == len(set(first_load))


def test_local_apps_manifest_loading_does_not_import_app_configs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Loading manifests for LOCAL_APPS should not validate/import app configs eagerly."""

    def _fail_if_called(_app_entry: str) -> None:
        raise AssertionError(
            "_validate_manifest_app_entry should not run during manifest loading"
        )

    monkeypatch.setattr(settings, "_validate_manifest_app_entry", _fail_if_called)

    loaded_apps = settings._load_local_apps_from_manifests()

    assert loaded_apps


@pytest.mark.parametrize(
    ("manifest_module", "app_entry"),
    _manifest_entries_with_source(),
    ids=lambda entry: entry,
)
def test_manifest_entry_resolves_to_importable_app_config(
    manifest_module: str,
    app_entry: str,
) -> None:
    """Every discovered manifest entry should resolve through AppConfig.create."""

    try:
        settings._validate_manifest_app_entry(app_entry)
    except ImproperlyConfigured as exc:  # pragma: no cover - assertion path only
        raise AssertionError(
            f"Manifest '{manifest_module}' contains invalid DJANGO_APPS entry '{app_entry}'."
        ) from exc


def test_manifest_entries_are_unique_after_normalization() -> None:
    """Regression: no manifest may register duplicate normalized app entries."""

    entries_by_app: dict[str, list[str]] = {}
    for manifest_module, app_entry in _manifest_entries_with_source():
        entries_by_app.setdefault(app_entry, []).append(manifest_module)

    duplicates = {
        app_entry: sorted(modules)
        for app_entry, modules in entries_by_app.items()
        if len(modules) > 1
    }

    assert not duplicates, (
        "Duplicate normalized DJANGO_APPS entries detected: "
        + ", ".join(
            f"{app_entry} (manifests: {', '.join(modules)})"
            for app_entry, modules in sorted(duplicates.items())
        )
    )
