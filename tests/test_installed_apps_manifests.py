"""Tests for manifest-based Django app discovery in settings."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

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
        except ValueError as exc:
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

    return []


def _expected_local_apps_from_manifest_files() -> list[str]:
    """Build expected local apps from manifest files to avoid brittle hard-coded lists."""

    app_entries: list[str] = []
    manifests = sorted((settings.BASE_DIR / "apps").rglob("manifest.py"))
    for manifest_path in manifests:
        app_entries.extend(_extract_manifest_apps(manifest_path))
    return app_entries


def test_local_apps_manifest_loading_is_complete_and_deterministic() -> None:
    """Regression: manifest loading should reproduce the project app list deterministically."""

    expected_local_apps = _expected_local_apps_from_manifest_files()
    first_load = settings._load_local_apps_from_manifests()
    second_load = settings._load_local_apps_from_manifests()

    assert set(first_load) == set(expected_local_apps)
    assert set(second_load) == set(expected_local_apps)
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


def test_local_apps_manifests_resolve_to_importable_app_configs() -> None:
    """Every loaded manifest entry should resolve through AppConfig.create."""

    for app_entry in settings._load_local_apps_from_manifests():
        settings._validate_manifest_app_entry(app_entry)
