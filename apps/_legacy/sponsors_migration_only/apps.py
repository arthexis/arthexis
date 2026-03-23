"""Application config for the legacy sponsors migration-only app."""

from django.apps import AppConfig


class SponsorsMigrationOnlyConfig(AppConfig):
    """Keep sponsor migrations available while the runtime app is removed."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps._legacy.sponsors_migration_only"
    label = "sponsors"
    verbose_name = "Sponsors (migration only)"
