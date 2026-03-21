"""Application config for the legacy socials migration-only app."""

from django.apps import AppConfig


class SocialsMigrationOnlyConfig(AppConfig):
    """Keep socials migrations available after removing the runtime app package."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps._legacy.socials_migration_only"
    label = "socials"
    verbose_name = "Social Integrations (migration only)"
