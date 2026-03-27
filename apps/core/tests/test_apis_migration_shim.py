"""Regression tests for the retired apis migration shim wiring."""

from __future__ import annotations

import importlib

from django.conf import settings

from config.settings import apps as settings_apps


def test_apis_runtime_app_not_listed_in_installed_apps():
    """The retired runtime apis app should stay out of INSTALLED_APPS."""

    assert "apps.apis" not in settings.INSTALLED_APPS


def test_apis_runtime_app_not_listed_in_project_local_apps():
    """The retired runtime apis app should stay out of PROJECT_LOCAL_APPS."""

    assert "apps.apis" not in settings_apps.PROJECT_LOCAL_APPS


def test_apis_legacy_app_listed_for_migration_compatibility():
    """The apis migration-only app should remain in legacy app wiring."""

    legacy_app = "apps._legacy.apis_migration_only.apps.ApisMigrationOnlyConfig"

    assert legacy_app in settings.LEGACY_MIGRATION_APPS


def test_apis_migration_modules_maps_to_legacy_shim():
    """MIGRATION_MODULES should route apis to the legacy shim package."""

    assert settings_apps.MIGRATION_MODULES["apis"] == (
        "apps._legacy.apis_migration_only.migrations"
    )


def test_apis_historical_migration_chain_is_loadable():
    """The shim should expose the historical apis migration modules."""

    migration_module = importlib.import_module(
        "apps._legacy.apis_migration_only.migrations.0004_record_evergo_fixture_primary_keys"
    )

    assert migration_module.Migration.dependencies == [
        ("apis", "0003_expand_evergo_api_explorer_endpoints")
    ]
