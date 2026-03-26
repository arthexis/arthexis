"""Regression tests for the retired prototypes migration shim wiring."""

from __future__ import annotations

import importlib

from django.conf import settings

from config.settings import apps as settings_apps


def test_prototypes_runtime_app_not_listed_in_installed_apps():
    """The retired runtime prototypes app should stay out of INSTALLED_APPS."""

    assert "apps.prototypes" not in settings.INSTALLED_APPS


def test_prototypes_legacy_app_listed_for_migration_compatibility():
    """The prototypes migration-only app should remain in legacy app wiring."""

    legacy_app = (
        "apps._legacy.prototypes_migration_only.apps."
        "PrototypesMigrationOnlyConfig"
    )

    assert legacy_app in settings.LEGACY_MIGRATION_APPS


def test_prototypes_migration_modules_maps_to_legacy_shim():
    """MIGRATION_MODULES should route prototypes to the legacy shim package."""

    assert settings_apps.MIGRATION_MODULES["prototypes"] == (
        "apps._legacy.prototypes_migration_only.migrations"
    )


def test_prototypes_historical_migration_chain_is_loadable():
    """The shim should expose the historical prototypes migration modules."""

    migration_module = importlib.import_module(
        "apps._legacy.prototypes_migration_only.migrations.0002_retire_prototype_runtime"
    )

    assert migration_module.Migration.dependencies == [("prototypes", "0001_initial")]
