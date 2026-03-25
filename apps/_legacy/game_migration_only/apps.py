"""Application config for the legacy game migration-only app."""

from django.apps import AppConfig


class GameMigrationOnlyConfig(AppConfig):
    """Keep game migrations available while runtime code stays removed."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps._legacy.game_migration_only"
    label = "game"
    verbose_name = "Game (migration only)"
