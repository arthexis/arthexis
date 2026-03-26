"""Tests for major-version legacy shim pruning in settings wiring."""

from __future__ import annotations

from config.settings import apps


def test_drop_legacy_app_entries_keeps_shims_on_zero_major() -> None:
    """0.x versions should keep legacy app shims enabled."""

    entries = [
        "apps._legacy.shortcuts_migration_only.apps.ShortcutsMigrationOnlyConfig",
        "apps.projects",
    ]

    assert (
        apps._drop_legacy_app_entries(
            entries,
            apps.LEGACY_MIGRATION_APPS,
            version="0.2.3",
        )
        == entries
    )


def test_drop_legacy_app_entries_prunes_shims_on_major_upgrade() -> None:
    """1.x+ versions should remove all apps._legacy app entries."""

    entries = [
        "apps._legacy.shortcuts_migration_only.apps.ShortcutsMigrationOnlyConfig",
        "apps.projects",
        "config.legacy_mermaid",
    ]

    assert apps._drop_legacy_app_entries(
        entries,
        apps.LEGACY_MIGRATION_APPS,
        version="1.0.0",
    ) == ["apps.projects"]


def test_drop_legacy_migration_modules_keeps_shims_on_zero_major() -> None:
    """0.x versions should keep legacy migration-module shims enabled."""

    modules = {
        "shortcuts": "apps._legacy.shortcuts_migration_only.migrations",
        "sites": "apps.core.sites_migrations",
    }

    assert apps._drop_legacy_migration_modules(modules, version="0.2.3") == modules


def test_drop_legacy_migration_modules_prunes_legacy_targets() -> None:
    """Major upgrades should drop migration-module routes under apps._legacy."""

    modules = {
        "shortcuts": "apps._legacy.shortcuts_migration_only.migrations",
        "sites": "apps.core.sites_migrations",
    }

    assert apps._drop_legacy_migration_modules(modules, version="1.0.0") == {
        "sites": "apps.core.sites_migrations"
    }
