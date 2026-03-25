# Shortcuts runtime removal and legacy migration path

The runtime `shortcuts` Django app has been removed from automatic discovery.
A legacy migration-only shim now preserves the historical migration chain under `apps/_legacy/shortcuts_migration_only/`.

## Upgrade guidance

1. Keep `apps._legacy.shortcuts_migration_only.apps.ShortcutsMigrationOnlyConfig` in `LEGACY_MIGRATION_APPS`.
2. Keep `MIGRATION_MODULES["shortcuts"] = "apps._legacy.shortcuts_migration_only.migrations"`.
3. Run `python manage.py migrate` before moving to the next major cleanup branch.
