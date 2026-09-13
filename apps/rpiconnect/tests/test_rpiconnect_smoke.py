"""Retirement invariants for the migration-only rpiconnect shell."""

from importlib import import_module
from pathlib import Path

from django.apps import apps


def test_rpiconnect_shell_remains_importable_for_migrations() -> None:
    """Keep only the app metadata needed to apply historical migrations."""

    assert import_module("apps.rpiconnect.apps")
    assert import_module("apps.rpiconnect.manifest")


def test_rpiconnect_exposes_no_root_routes() -> None:
    """Retired fleet-management endpoints must not be mounted."""

    routes = import_module("apps.rpiconnect.routes")
    assert routes.ROOT_URLPATTERNS == []


def test_rpiconnect_registers_no_runtime_models() -> None:
    """The terminal migration and runtime app state both have no models."""

    app_config = apps.get_app_config("rpiconnect")
    assert list(app_config.get_models()) == []


def test_rpiconnect_has_terminal_retirement_migration() -> None:
    """Existing databases must get a schema-removal step before app deletion."""

    migration = Path(__file__).parents[1] / "migrations" / "0003_retire_rpiconnect.py"
    text = migration.read_text(encoding="utf-8")
    assert '("rpiconnect", "0002_initial")' in text
    for model_name in (
        "ConnectCampaignEvent",
        "ConnectIngestionEvent",
        "ConnectUpdateDeployment",
        "ConnectUpdateCampaign",
        "ConnectDevice",
        "ConnectImageRelease",
        "ConnectAccount",
    ):
        assert f'DeleteModel(name="{model_name}")' in text
