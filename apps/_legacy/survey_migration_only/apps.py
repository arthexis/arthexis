"""Application config for the legacy survey migration-only app."""

from django.apps import AppConfig


class SurveyMigrationOnlyConfig(AppConfig):
    """Keep survey migrations available while the runtime app is removed."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps._legacy.survey_migration_only"
    label = "survey"
    verbose_name = "Survey (migration only)"
