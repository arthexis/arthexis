"""Application config for the legacy prompts migration-only app."""

from django.apps import AppConfig


class PromptsMigrationOnlyConfig(AppConfig):
    """Keep prompt migrations available while the runtime app stays removed."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps._legacy.prompts_migration_only"
    label = "prompts"
    verbose_name = "Prompts (migration only)"
