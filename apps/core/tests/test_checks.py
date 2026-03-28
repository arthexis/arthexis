"""Tests for system check invariants."""

from __future__ import annotations

from apps.core import checks


def test_no_legacy_migration_apps_registered_passes_without_legacy_apps(monkeypatch):
    """The 1.0 invariant passes when no legacy app shims are installed."""

    monkeypatch.setattr(
        checks.settings,
        "INSTALLED_APPS",
        ["apps.core", "apps.ocpp"],
        raising=False,
    )

    assert checks.no_legacy_migration_apps_registered(None) == []


def test_no_legacy_migration_apps_registered_rejects_legacy_apps(monkeypatch):
    """The 1.0 invariant reports legacy migration-only apps if they appear."""

    monkeypatch.setattr(
        checks.settings,
        "INSTALLED_APPS",
        [
            "apps.core",
            "apps._legacy.sponsors_migration_only.apps.SponsorsMigrationOnlyConfig",
        ],
        raising=False,
    )

    errors = checks.no_legacy_migration_apps_registered(None)

    assert len(errors) == 1
    assert errors[0].id == "core.E100"
    assert "apps._legacy.sponsors_migration_only" in errors[0].obj
