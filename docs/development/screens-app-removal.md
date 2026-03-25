# Screens runtime removal and legacy migration path

The runtime `screens` Django app has been removed from automatic discovery.
A legacy migration-only shim now preserves the historical migration chain under `apps/_legacy/screens_migration_only/`.

LCD utilities remain available through the `lcd` management command hosted in `apps.core.management.commands.lcd`.

## Upgrade guidance

1. Keep `apps._legacy.screens_migration_only.apps.ScreensMigrationOnlyConfig` in `LEGACY_MIGRATION_APPS`.
2. Keep `MIGRATION_MODULES["screens"] = "apps._legacy.screens_migration_only.migrations"`.
3. Run `python manage.py migrate` before moving to the next major cleanup branch.
