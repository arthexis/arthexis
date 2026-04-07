"""Tests for external app database wiring."""

from config.settings.database import build_external_sqlite_databases
from config.settings.external_dbs import external_app_database_alias


def test_build_external_sqlite_databases_uses_work_dbs_dir(settings):
    configs = build_external_sqlite_databases(
        [
            "arthexis_plugin_sample.apps.ArthexisPluginSampleConfig",
            "vendor.sync.apps.SyncConfig",
        ]
    )

    assert sorted(configs.keys()) == [
        "external_arthexis_plugin_sample",
        "external_sync",
    ]
    assert str(configs["external_arthexis_plugin_sample"]["NAME"]).endswith(
        "work/dbs/external_arthexis_plugin_sample.sqlite3"
    )
    assert str(configs["external_sync"]["NAME"]).endswith(
        "work/dbs/external_sync.sqlite3"
    )


def test_external_alias_falls_back_when_path_has_no_suffix_token():
    alias = external_app_database_alias("...", fallback_index=4)

    assert alias == "external_app_4"


def test_external_router_routes_external_model(settings):
    settings.ARTHEXIS_EXTERNAL_APPS = [
        "arthexis_plugin_sample.apps.ArthexisPluginSampleConfig"
    ]

    from apps.core.dbrouters import ExternalAppDatabaseRouter

    class PluginModel:
        __module__ = "arthexis_plugin_sample.models"

    router = ExternalAppDatabaseRouter()

    assert router.db_for_read(PluginModel) == "external_arthexis_plugin_sample"
    assert router.db_for_write(PluginModel) == "external_arthexis_plugin_sample"
    assert router.allow_migrate("default", "sample", model=PluginModel) is False
    assert (
        router.allow_migrate(
            "external_arthexis_plugin_sample",
            "sample",
            model=PluginModel,
        )
        is True
    )
