from django.apps import AppConfig


class NetmeshMigrationOnlyConfig(AppConfig):
    """Legacy netmesh app config used only for migration compatibility."""

    name = "apps._legacy.netmesh_migration_only"
    label = "netmesh"
    verbose_name = "Netmesh (migration only)"
