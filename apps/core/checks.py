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
    "fitbit_fitbithealthsample",
    "fitbit_fitbitnetmessagedelivery",
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
                "Upgrade through an earlier Arthexis release that still ships "
                "apps._legacy.fitbit_migration_only, run `python manage.py migrate`, "
                f"confirm `fitbit.{FITBIT_REMOVAL_MIGRATION}` is recorded, and then retry "
                "this release."
            ),
            obj="; ".join(details) if details else None,
            id="core.E001",
        )
    ]
