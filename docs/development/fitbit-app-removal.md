# Fitbit app removal and legacy upgrade path

The runtime `fitbit` package has been removed from automatic discovery.
Upgrade compatibility is provided by `apps._legacy.fitbit_migration_only`.

## Upgrade guidance

For installations that still contain historical Fitbit migration state:

1. Ensure `apps._legacy.fitbit_migration_only.apps.FitbitMigrationOnlyConfig` is present in `LEGACY_MIGRATION_APPS`.
2. Ensure `MIGRATION_MODULES["fitbit"]` points to `apps._legacy.fitbit_migration_only.migrations`.
3. Run `python manage.py migrate` and confirm `fitbit.0002_remove_fitbit_models` is recorded.

The migration archives historical Fitbit tables by renaming them to:

- `fitbit_archived_fitbitconnection`
- `fitbit_archived_fitbithealthsample`
- `fitbit_archived_fitbitnetmessagedelivery`

This keeps rollback support while removing the runtime app surface.
