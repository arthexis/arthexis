"""Application config for the legacy socials migration-only app."""

from django.apps import AppConfig


class SocialsMigrationOnlyConfig(AppConfig):
    """Keep the retired socials migration chain available for historical installs."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps._legacy.socials_migration_only"
    label = "socials"
    verbose_name = "Socials (migration only)"
