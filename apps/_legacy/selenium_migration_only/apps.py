"""Application config for the legacy selenium migration-only app."""

from django.apps import AppConfig


class SeleniumMigrationOnlyConfig(AppConfig):
    """Keep the retired selenium migration chain available for historical installs."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps._legacy.selenium_migration_only"
    label = "selenium"
    verbose_name = "Selenium (migration only)"
