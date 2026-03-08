"""Regression tests for the create_local_app management command."""

from __future__ import annotations

import io
from importlib import util
from pathlib import Path
from types import ModuleType

import pytest
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError


def _create_apps_package(apps_dir: Path) -> None:
    apps_dir.mkdir(parents=True, exist_ok=True)
    (apps_dir / "__init__.py").write_text('"""Project application packages."""\n', encoding="utf-8")



def _load_module_from_path(module_name: str, path: Path) -> ModuleType:
    """Load a module directly from a generated file path."""

    spec = util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"Unable to build import spec for {path}")

    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_create_local_app_generates_expected_scaffold(tmp_path):
    """The command should generate the expected app structure and scaffolding files."""

    apps_dir = tmp_path / "apps"
    _create_apps_package(apps_dir)
    settings.BASE_DIR = tmp_path
    settings.APPS_DIR = apps_dir

    stdout = io.StringIO()
    call_command("create_local_app", "billing", stdout=stdout)

    expected_files = [
        apps_dir / "billing" / "__init__.py",
        apps_dir / "billing" / "apps.py",
        apps_dir / "billing" / "models.py",
        apps_dir / "billing" / "admin.py",
        apps_dir / "billing" / "manifest.py",
        apps_dir / "billing" / "routes.py",
        apps_dir / "billing" / "migrations" / "__init__.py",
        apps_dir / "billing" / "tests" / "test_billing_smoke.py",
    ]
    for expected_file in expected_files:
        assert expected_file.exists(), f"Missing generated file: {expected_file}"

    manifest_text = (apps_dir / "billing" / "manifest.py").read_text(encoding="utf-8")
    assert 'DJANGO_APPS = [\n    "apps.billing",\n]\n' in manifest_text

    admin_text = (apps_dir / "billing" / "admin.py").read_text(encoding="utf-8")
    assert "@admin.register(BillingItem)" in admin_text

    output = stdout.getvalue()
    assert "makemigrations billing" in output
    assert "python manage.py migrate" in output
    assert "apps/billing/routes.py" in output


def test_create_local_app_generated_modules_are_importable(tmp_path):
    """Generated Python modules should be importable from the temporary apps package."""

    apps_dir = tmp_path / "apps"
    _create_apps_package(apps_dir)
    settings.BASE_DIR = tmp_path
    settings.APPS_DIR = apps_dir

    call_command("create_local_app", "operations")

    apps_module = _load_module_from_path(
        "generated_operations_apps", apps_dir / "operations" / "apps.py"
    )
    manifest_module = _load_module_from_path(
        "generated_operations_manifest", apps_dir / "operations" / "manifest.py"
    )
    models_module = _load_module_from_path(
        "generated_operations_models", apps_dir / "operations" / "models.py"
    )

    assert apps_module.OperationsConfig.name == "apps.operations"
    assert manifest_module.DJANGO_APPS == ["apps.operations"]
    assert hasattr(models_module, "OperationsItem")


def test_create_local_app_rejects_invalid_name(tmp_path):
    """Invalid app names should raise a command error."""

    apps_dir = tmp_path / "apps"
    _create_apps_package(apps_dir)
    settings.BASE_DIR = tmp_path
    settings.APPS_DIR = apps_dir

    with pytest.raises(CommandError, match="Invalid app name"):
        call_command("create_local_app", "Bad-Name")


def test_create_local_app_rejects_existing_app(tmp_path):
    """An existing app directory should not be overwritten."""

    apps_dir = tmp_path / "apps"
    _create_apps_package(apps_dir)
    (apps_dir / "core").mkdir(parents=True)

    settings.BASE_DIR = tmp_path
    settings.APPS_DIR = apps_dir

    with pytest.raises(CommandError, match="App already exists"):
        call_command("create_local_app", "core")
