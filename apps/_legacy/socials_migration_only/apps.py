"""Application config for the legacy socials migration-only app."""

from django.apps import AppConfig


class SocialsMigrationOnlyConfig(AppConfig):
    """Keep historical socials migrations available after runtime removal."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps._legacy.socials_migration_only"
    label = "socials"
    verbose_name = "Social integrations (migration only)"
