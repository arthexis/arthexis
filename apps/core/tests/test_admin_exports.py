from __future__ import annotations

import pytest

from apps.core import admin as admin_exports


def test_core_admin_all_exports_skip_disabled_optional_apps():
    def fake_is_installed(app_name: str) -> bool:
        return app_name not in {"apps.energy", "apps.odoo"}

    exports = admin_exports._build_all_exports(is_installed=fake_is_installed)

    assert "EmailInboxAdmin" in exports
    assert "RFIDImportForm" in exports
    assert "CustomerAccountRFIDForm" not in exports
    assert "OdooEmployeeAdminForm" not in exports


def test_core_admin_getattr_rejects_disabled_optional_export(monkeypatch):
    monkeypatch.delitem(
        admin_exports.__dict__,
        "OdooEmployeeAdminForm",
        raising=False,
    )
    monkeypatch.setattr(
        admin_exports.django_apps,
        "is_installed",
        lambda app_name: app_name != "apps.odoo",
    )

    with pytest.raises(AttributeError, match="apps.odoo is not installed"):
        admin_exports.__getattr__("OdooEmployeeAdminForm")
