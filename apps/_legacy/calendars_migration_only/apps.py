"""Application config for the legacy calendars migration-only app."""

from django.apps import AppConfig


class CalendarsMigrationOnlyConfig(AppConfig):
    """Keep calendars migrations available while runtime code stays removed."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps._legacy.calendars_migration_only"
    label = "calendars"
    verbose_name = "Calendars (migration only)"
