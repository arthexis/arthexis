from __future__ import annotations

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


OLD_TARGET = [("nginx", "0002_remove_siteconfiguration_certificate")]
NEW_TARGET = [("nginx", "0003_delete_siteconfiguration")]


def _apps_for(targets):
    executor = MigrationExecutor(connection)
    return executor, executor.loader.project_state(targets).apps


@pytest.mark.django_db(transaction=True)
def test_site_configuration_is_removed_when_upgrading_from_0002():
    """Existing nginx configuration rows are retired through the migration graph."""

    initial_executor = MigrationExecutor(connection)
    latest_targets = initial_executor.loader.graph.leaf_nodes()

    try:
        initial_executor.migrate(OLD_TARGET)
        _, old_apps = _apps_for(OLD_TARGET)
        SiteConfiguration = old_apps.get_model("nginx", "SiteConfiguration")
        table_name = SiteConfiguration._meta.db_table

        SiteConfiguration.objects.create(name="migration-retirement")
        assert SiteConfiguration.objects.filter(name="migration-retirement").exists()
        assert table_name in connection.introspection.table_names()

        forward_executor = MigrationExecutor(connection)
        forward_executor.migrate(NEW_TARGET)
        _, new_apps = _apps_for(NEW_TARGET)

        with pytest.raises(LookupError):
            new_apps.get_model("nginx", "SiteConfiguration")
        assert table_name not in connection.introspection.table_names()
    finally:
        restore_executor = MigrationExecutor(connection)
        restore_executor.migrate(latest_targets)


@pytest.mark.django_db(transaction=True)
def test_nginx_retirement_migrations_apply_from_zero():
    """A clean database can traverse the complete nginx migration history."""

    initial_executor = MigrationExecutor(connection)
    latest_targets = initial_executor.loader.graph.leaf_nodes()

    try:
        initial_executor.migrate([("nginx", None)])
        assert "nginx_siteconfiguration" not in connection.introspection.table_names()

        fresh_executor = MigrationExecutor(connection)
        fresh_executor.migrate(NEW_TARGET)
        _, fresh_apps = _apps_for(NEW_TARGET)

        with pytest.raises(LookupError):
            fresh_apps.get_model("nginx", "SiteConfiguration")
        assert "nginx_siteconfiguration" not in connection.introspection.table_names()
    finally:
        restore_executor = MigrationExecutor(connection)
        restore_executor.migrate(latest_targets)
