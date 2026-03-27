"""Application config for the legacy nmcli migration-only app."""

from django.apps import AppConfig


class NmcliMigrationOnlyConfig(AppConfig):
    """Keep nmcli migrations available while runtime code stays removed."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps._legacy.nmcli_migration_only"
    label = "nmcli"
    verbose_name = "NMCLI (migration only)"
