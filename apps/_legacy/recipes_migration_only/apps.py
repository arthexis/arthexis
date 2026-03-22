"""Application config for the legacy recipes migration-only app."""

from django.apps import AppConfig


class RecipesMigrationOnlyConfig(AppConfig):
    """Keep recipe migrations available while runtime recipe entrypoints stay removed."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps._legacy.recipes_migration_only"
    label = "recipes"
    verbose_name = "Recipes (migration only)"
