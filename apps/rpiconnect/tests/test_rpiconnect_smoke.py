"""Smoke tests for the temporarily retained Raspberry Pi Connect app."""

from importlib import import_module


def test_rpiconnect_imports_for_migration_compatibility() -> None:
    """The app remains importable only while its schema is being retired."""

    assert import_module("apps.rpiconnect.apps")
    assert import_module("apps.rpiconnect.manifest")
    assert import_module("apps.rpiconnect.models")


def test_rpiconnect_exposes_no_root_routes() -> None:
    """Retired fleet-management endpoints must not be mounted."""

    routes = import_module("apps.rpiconnect.routes")
    assert routes.ROOT_URLPATTERNS == []
