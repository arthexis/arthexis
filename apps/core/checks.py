"""System checks for repository-wide runtime and upgrade safeguards."""

from __future__ import annotations

from django.core.checks import Error, register
from django.db import connections
from django.db.migrations.recorder import MigrationRecorder
from django.db.utils import DatabaseError, OperationalError, ProgrammingError

FITBIT_REMOVAL_MIGRATION = "0002_remove_fitbit_models"
FITBIT_INITIAL_MIGRATION = "0001_initial"
LEGACY_FITBIT_TABLES = (
    "fitbit_fitbitconnection",
    "fitbit_archived_fitbitconnection",
    "fitbit_fitbithealthsample",
    "fitbit_archived_fitbithealthsample",
    "fitbit_fitbitnetmessagedelivery",
    "fitbit_archived_fitbitnetmessagedelivery",
)
GAME_REMOVAL_MIGRATION = "0002_archive_and_drop_avatar"
GAME_INITIAL_MIGRATION = "0001_initial"
LEGACY_GAME_TABLES = (
    "game_avatar",
    "game_archived_avatar",
)


@register()
def fitbit_cleanup_migration_was_applied(app_configs, **kwargs):
    """Reject upgrades that skipped the historical Fitbit cleanup migration.

    Parameters:
        app_configs: Django app configs supplied by the system check framework.
        **kwargs: Additional keyword arguments from the check framework.

    Returns:
        list[Error]: Validation errors when the database still reflects a
            pre-cleanup Fitbit migration state.

    Raises:
        None.
    """

    del app_configs, kwargs

    connection = connections["default"]
    recorder = MigrationRecorder(connection)
    try:
        if not recorder.has_table():
            return []

        applied_fitbit_migrations = set(
            recorder.migration_qs.filter(app="fitbit").values_list("name", flat=True)
        )
        legacy_fitbit_tables = sorted(
            table_name
            for table_name in connection.introspection.table_names()
            if table_name in LEGACY_FITBIT_TABLES
        )
    except (DatabaseError, OperationalError, ProgrammingError):
        return []

    if FITBIT_REMOVAL_MIGRATION in applied_fitbit_migrations:
        return []

    if not applied_fitbit_migrations and not legacy_fitbit_tables:
        return []

    details: list[str] = []
    if applied_fitbit_migrations:
        details.append(
            "applied Fitbit migrations: "
            + ", ".join(sorted(applied_fitbit_migrations))
        )
    if legacy_fitbit_tables:
        details.append("legacy Fitbit tables: " + ", ".join(legacy_fitbit_tables))

    return [
        Error(
            "This database never completed the historical Fitbit cleanup migration.",
            hint=(
                "Ensure apps._legacy.fitbit_migration_only is enabled, run "
                "`python manage.py migrate`, "
                f"confirm `fitbit.{FITBIT_REMOVAL_MIGRATION}` is recorded, and then retry "
                "this release."
            ),
            obj="; ".join(details) if details else None,
            id="core.E001",
        )
    ]


@register()
def game_cleanup_migration_was_applied(app_configs, **kwargs):
    """Reject upgrades that skipped the historical game cleanup migration."""

    del app_configs, kwargs

    connection = connections["default"]
    recorder = MigrationRecorder(connection)
    try:
        if not recorder.has_table():
            return []

        applied_game_migrations = set(
            recorder.migration_qs.filter(app="game").values_list("name", flat=True)
        )
        legacy_game_tables = sorted(
            table_name
            for table_name in connection.introspection.table_names()
            if table_name in LEGACY_GAME_TABLES
        )
    except (DatabaseError, OperationalError, ProgrammingError):
        return []

    if GAME_REMOVAL_MIGRATION in applied_game_migrations:
        return []

    if not applied_game_migrations and not legacy_game_tables:
        return []

    details: list[str] = []
    if applied_game_migrations:
        details.append("applied game migrations: " + ", ".join(sorted(applied_game_migrations)))
    if legacy_game_tables:
        details.append("legacy game tables: " + ", ".join(legacy_game_tables))

    return [
        Error(
            "This database never completed the historical game cleanup migration.",
            hint=(
                "Ensure apps._legacy.game_migration_only is enabled, run "
                "`python manage.py migrate`, "
                f"confirm `game.{GAME_REMOVAL_MIGRATION}` is recorded, and then retry "
                "this release."
            ),
            obj="; ".join(details) if details else None,
            id="core.E002",
        )
    ]
