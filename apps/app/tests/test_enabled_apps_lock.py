"""Tests for enabled application lock synchronization."""

from __future__ import annotations

import pytest

from apps.app.models import Application, _load_manifest_app_entries
from utils.enabled_apps_lock import get_enabled_apps_lock_path


@pytest.mark.django_db
def test_application_save_updates_enabled_apps_lock(settings):
    """Regression: saving application enablement rewrites the enabled-app lock file."""

    Application.objects.filter(name__in=["enabled-core", "enabled-site"]).delete()
    app = Application.objects.create(name="enabled-core", enabled=True)
    lock_path = get_enabled_apps_lock_path(settings.BASE_DIR)

    assert lock_path.exists()
    assert app.name in lock_path.read_text(encoding="utf-8").splitlines()

    app.enabled = False
    app.save(update_fields=["enabled"])

    assert app.name not in lock_path.read_text(encoding="utf-8").splitlines()


@pytest.mark.django_db
def test_application_delete_updates_enabled_apps_lock(settings):
    """Regression: deleting an enabled app rewrites the lock file without that entry."""

    Application.objects.filter(name__in=["enabled-core", "enabled-site"]).delete()
    first = Application.objects.create(name="enabled-core", enabled=True)
    second = Application.objects.create(name="enabled-site", enabled=True)
    lock_path = get_enabled_apps_lock_path(settings.BASE_DIR)

    first.delete()

    assert lock_path.exists()
    lock_entries = lock_path.read_text(encoding="utf-8").splitlines()
    assert first.name not in lock_entries
    assert second.name in lock_entries


@pytest.mark.django_db
def test_refresh_enabled_apps_lock_keeps_manifest_apps_without_rows(
    monkeypatch, settings
):
    """Regression: manifest apps without Application rows should stay in lock output."""

    Application.objects.filter(name__in=["enabled-core", "sites"]).delete()
    monkeypatch.setattr(
        "apps.app.models._load_manifest_app_entries",
        lambda: {"apps.meta", "apps.sites"},
    )

    Application.objects.create(name="enabled-core", enabled=True)
    lock_entries = (
        get_enabled_apps_lock_path(settings.BASE_DIR)
        .read_text(encoding="utf-8")
        .splitlines()
    )

    assert "enabled-core" in lock_entries
    assert "apps.meta" in lock_entries


@pytest.mark.django_db
def test_refresh_enabled_apps_lock_respects_disabled_manifest_labels(
    monkeypatch, settings
):
    """Regression: disabled labels should remove matching manifest app entries."""

    Application.objects.filter(name__in=["enabled-core", "sites"]).delete()
    monkeypatch.setattr(
        "apps.app.models._load_manifest_app_entries",
        lambda: {"apps.meta", "apps.sites"},
    )

    Application.objects.create(name="meta", enabled=False)
    Application.objects.create(name="enabled-core", enabled=True)
    lock_entries = (
        get_enabled_apps_lock_path(settings.BASE_DIR)
        .read_text(encoding="utf-8")
        .splitlines()
    )

    assert "apps.meta" not in lock_entries
    assert "enabled-core" in lock_entries


def test_load_manifest_app_entries_includes_runtime_and_legacy_migration_apps():
    """Manifest discovery should include runtime apps and migration-only shims."""

    manifest_app_entries = _load_manifest_app_entries()
    expected_apps = {
        "apps.classification",
        "apps.projects",
        "apps.special",
    }

    assert expected_apps.issubset(manifest_app_entries)
    retired_apps = {
        "calendars": "apps._legacy.calendars_migration_only.apps.CalendarsMigrationOnlyConfig",
        "screens": "apps._legacy.screens_migration_only.apps.ScreensMigrationOnlyConfig",
        "shortcuts": "apps._legacy.shortcuts_migration_only.apps.ShortcutsMigrationOnlyConfig",
        "smb": "apps._legacy.smb_migration_only.apps.SmbMigrationOnlyConfig",
    }
    for app_label, legacy_app in retired_apps.items():
        assert legacy_app in manifest_app_entries
        assert f"apps.{app_label}" not in manifest_app_entries

    assert "apps.game" not in manifest_app_entries
    assert (
        "apps._legacy.sponsors_migration_only.apps.SponsorsMigrationOnlyConfig"
        in manifest_app_entries
    )
    assert "apps.sponsors" not in manifest_app_entries
