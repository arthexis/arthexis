# SMB runtime removal and legacy migration path

The runtime `smb` Django app has been removed from automatic discovery.
A legacy migration-only shim now preserves the historical migration chain under `apps/_legacy/smb_migration_only/`.

## Upgrade guidance

1. Keep `apps._legacy.smb_migration_only.apps.SmbMigrationOnlyConfig` in `LEGACY_MIGRATION_APPS`.
2. Keep `MIGRATION_MODULES["smb"] = "apps._legacy.smb_migration_only.migrations"`.
3. Run `python manage.py migrate` before moving to the next major cleanup branch.
