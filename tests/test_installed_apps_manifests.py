"""Regression tests for Django INSTALLED_APPS assembly."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from django.apps import AppConfig

from config import settings
from config.settings import apps as app_settings

pytestmark = pytest.mark.regression


def test_local_apps_are_discovered_from_apps_py_modules() -> None:
    """Regression: local app discovery should mirror real Django app packages."""

    expected_entries = {
        ".".join(path.parent.relative_to(settings.BASE_DIR).parts)
        for path in (Path(settings.BASE_DIR) / "apps").rglob("apps.py")
    }

    discovered_entries = set(app_settings._load_local_apps())

    assert discovered_entries == expected_entries


def test_local_apps_are_importable_django_app_entries() -> None:
    """Every discovered local app entry should resolve through ``AppConfig.create``."""

    for app_entry in app_settings._load_local_apps():
        try:
            AppConfig.create(app_entry)
        except Exception as exc:  # pragma: no cover - assertion-only path
            raise AssertionError(f"Invalid local app entry {app_entry!r}.") from exc


def test_installed_apps_keep_django_defaults_before_local_apps() -> None:
    """Regression: keep standard Django startup app ordering intact."""

    first_apps = settings.INSTALLED_APPS[:6]
    assert first_apps == [
        "whitenoise.runserver_nostatic",
        "django.contrib.admin",
        "django.contrib.admindocs",
        "config.auth_app.AuthConfig",
        "django.contrib.contenttypes",
        "django.contrib.sessions",
    ]


def test_core_model_module_imports_without_app_label_runtime_error() -> None:
    """Regression: importing core models should not fail app registration checks."""

    module = importlib.import_module("apps.core.models.admin_notice")

    assert module.AdminNotice._meta.app_label == "core"
