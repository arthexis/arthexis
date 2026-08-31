"""Tests for external app database wiring."""

from pathlib import Path

from config.settings.database import build_external_sqlite_databases
from config.settings.external_dbs import external_app_database_alias


def _assert_work_db_path(configs, alias):
    path = Path(configs[alias]["NAME"])

    assert path.name == f"{alias}.sqlite3"
    assert path.parent.name == "dbs"
    assert path.parent.parent.name == "work"


def test_build_external_sqlite_databases_uses_work_dbs_dir():
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
    _assert_work_db_path(configs, "external_arthexis_plugin_sample")
    _assert_work_db_path(configs, "external_sync")


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


def test_external_router_uses_collision_safe_aliases(settings):
    settings.ARTHEXIS_EXTERNAL_APPS = [
        "foo_bar.apps.FooBarConfig",
        "foo-bar.apps.FooDashBarConfig",
    ]

    from apps.core.dbrouters import ExternalAppDatabaseRouter

    class FirstPluginModel:
        __module__ = "foo_bar.models"

    class SecondPluginModel:
        __module__ = "foo-bar.models"

    router = ExternalAppDatabaseRouter()

    assert router.db_for_read(FirstPluginModel) == "external_foo_bar"
    assert router.db_for_read(SecondPluginModel) == "external_foo_bar_2"


def test_external_router_ignores_non_external_model(settings):
    settings.ARTHEXIS_EXTERNAL_APPS = [
        "arthexis_plugin_sample.apps.ArthexisPluginSampleConfig"
    ]

    from apps.core.dbrouters import ExternalAppDatabaseRouter

    class CoreModel:
        __module__ = "apps.core.models"

    router = ExternalAppDatabaseRouter()

    assert router.db_for_read(CoreModel) is None
    assert router.db_for_write(CoreModel) is None
    assert router.allow_migrate("external_arthexis_plugin_sample", "core", model=CoreModel) is False
