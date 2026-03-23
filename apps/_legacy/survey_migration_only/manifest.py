"""Manifest entries for the legacy survey migration-only app."""

# Keep this declarative mirror synchronized with config/settings/apps.py:
# LEGACY_MIGRATION_APPS.

DJANGO_APPS = [
    "apps._legacy.survey_migration_only.apps.SurveyMigrationOnlyConfig",
]
