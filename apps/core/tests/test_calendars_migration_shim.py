"""Regression tests for the retired calendars migration shim wiring."""

from __future__ import annotations

import importlib

from django.conf import settings

from config.settings import apps as settings_apps


def test_calendars_runtime_app_not_listed_in_installed_apps():
    """The retired runtime calendars app should stay out of INSTALLED_APPS."""

    assert "apps.calendars" not in settings.INSTALLED_APPS


def test_calendars_legacy_app_listed_for_migration_compatibility():
    """The calendars migration-only app should remain in legacy app wiring."""

    legacy_app = "apps._legacy.calendars_migration_only.apps.CalendarsMigrationOnlyConfig"

    assert legacy_app in settings.LEGACY_MIGRATION_APPS


def test_calendars_migration_modules_maps_to_legacy_shim():
    """MIGRATION_MODULES should route calendars to the legacy shim package."""

    assert settings_apps.MIGRATION_MODULES["calendars"] == (
        "apps._legacy.calendars_migration_only.migrations"
    )


def test_calendars_historical_migration_chain_is_loadable():
    """The shim should expose the historical calendars migration modules."""

    migration_module = importlib.import_module(
        "apps._legacy.calendars_migration_only.migrations.0003_delete_googlecalendar"
    )

    assert migration_module.Migration.dependencies == [
        ("calendars", "0002_rework_calendars_for_outbound_push"),
    ]
