# Retiring the `apis` app

The `apis` runtime app has been retired and moved to a migration-only legacy shim.

During the 0.x line:

1. Keep `apps._legacy.apis_migration_only.apps.ApisMigrationOnlyConfig` in `LEGACY_MIGRATION_APPS`.
2. Keep `MIGRATION_MODULES["apis"] = "apps._legacy.apis_migration_only.migrations"`.

For ongoing integration workflows, use `apps.evergo` and the related domain apps instead of the retired API Explorer runtime app.

At the next major line (1.x+), those entries can be pruned by the existing legacy-shim drop logic.
