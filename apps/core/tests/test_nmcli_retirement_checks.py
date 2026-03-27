"""Regression tests for nmcli retirement system checks."""

from __future__ import annotations

from apps.core import checks


class _SettingsStub:
    def __init__(self, *, installed_apps, migration_modules):
        self.INSTALLED_APPS = installed_apps
        self.MIGRATION_MODULES = migration_modules


def test_nmcli_retirement_check_passes_for_expected_wiring(monkeypatch):
    """Check should pass when nmcli runtime is retired and shim wiring is present."""

    monkeypatch.setattr(
        checks,
        "settings",
        _SettingsStub(
            installed_apps=["apps.core"],
            migration_modules={"nmcli": checks.NMCLI_LEGACY_MIGRATION_MODULE},
        ),
    )

    assert checks.nmcli_runtime_retirement_is_consistent(None) == []


def test_nmcli_retirement_check_flags_runtime_reenable(monkeypatch):
    """Check should return a dedicated error when runtime nmcli app is re-enabled."""

    monkeypatch.setattr(
        checks,
        "settings",
        _SettingsStub(
            installed_apps=["apps.core", "apps.nmcli"],
            migration_modules={"nmcli": checks.NMCLI_LEGACY_MIGRATION_MODULE},
        ),
    )

    errors = checks.nmcli_runtime_retirement_is_consistent(None)

    assert [error.id for error in errors] == ["core.E003"]


def test_nmcli_retirement_check_flags_migration_module_drift(monkeypatch):
    """Check should return a dedicated error when MIGRATION_MODULES drifts."""

    monkeypatch.setattr(
        checks,
        "settings",
        _SettingsStub(
            installed_apps=["apps.core"],
            migration_modules={"nmcli": "apps.nmcli.migrations"},
        ),
    )

    errors = checks.nmcli_runtime_retirement_is_consistent(None)

    assert [error.id for error in errors] == ["core.E004"]
