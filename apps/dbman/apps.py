from django.apps import AppConfig


class DbmanConfig(AppConfig):
    """Application configuration for database manager models."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.dbman"
    verbose_name = "Database Manager"
