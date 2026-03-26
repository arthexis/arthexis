# SMB runtime removal and legacy migration path

The runtime `smb` Django app has been retired and removed from the runtime package set.
A legacy migration-only shim now preserves the historical migration chain under `apps/_legacy/smb_migration_only/`.

## Operator note

- Runtime SMB features are retired and no longer load through runtime app wiring.
- Historical SMB migrations remain supported through `apps._legacy.smb_migration_only`.
- Keep the SMB migration-only shim through the current major line and drop it when preparing the next major-version cleanup branch.

## Upgrade guidance

1. Keep `apps._legacy.smb_migration_only.apps.SmbMigrationOnlyConfig` in `LEGACY_MIGRATION_APPS`.
2. Keep `MIGRATION_MODULES["smb"] = "apps._legacy.smb_migration_only.migrations"`.
3. Run `python manage.py migrate` before moving to the next major cleanup branch.
