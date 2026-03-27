"""Regression tests for the retired nmcli migration shim wiring."""

from __future__ import annotations

import importlib

from django.conf import settings

from config.settings import apps as settings_apps


def test_nmcli_runtime_app_not_listed_in_installed_apps():
    """The retired runtime nmcli app should stay out of INSTALLED_APPS."""

    assert "apps.nmcli" not in settings.INSTALLED_APPS


def test_nmcli_legacy_app_listed_for_migration_compatibility():
    """The nmcli migration-only app should remain in legacy app wiring."""

    legacy_app = "apps._legacy.nmcli_migration_only.apps.NmcliMigrationOnlyConfig"

    assert legacy_app in settings.LEGACY_MIGRATION_APPS


def test_nmcli_migration_modules_maps_to_legacy_shim():
    """MIGRATION_MODULES should route nmcli to the legacy shim package."""

    assert settings_apps.MIGRATION_MODULES["nmcli"] == (
        "apps._legacy.nmcli_migration_only.migrations"
    )


def test_nmcli_historical_migration_chain_is_loadable():
    """The shim should expose the historical nmcli migration modules."""

    migration_module = importlib.import_module(
        "apps._legacy.nmcli_migration_only.migrations.0003_alter_apclient_id"
    )

    assert migration_module.Migration.dependencies == [("nmcli", "0002_apclient")]
