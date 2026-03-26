# Prototypes app removal and migration compatibility

The runtime `prototypes` Django app has been removed from active app wiring and replaced with the legacy migration-only app `apps._legacy.prototypes_migration_only.apps.PrototypesMigrationOnlyConfig`.

## Runtime status

- Runtime app discovery no longer includes `apps.prototypes`.
- Runtime-only wiring for admin registration, management command exposure, and manifest registration has been retired.
- Historical migrations continue to load through `MIGRATION_MODULES["prototypes"] = "apps._legacy.prototypes_migration_only.migrations"`.

## Operator note

Existing environments should upgrade through a release that includes this migration-only shim so the full historical `prototypes` migration chain remains runnable.
