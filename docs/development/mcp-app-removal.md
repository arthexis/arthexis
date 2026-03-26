# Retiring the `mcp` app

The `mcp` runtime app has been retired and moved to a migration-only legacy shim.

During the 0.x line:

1. Keep `apps._legacy.mcp_migration_only.apps.McpMigrationOnlyConfig` in `LEGACY_MIGRATION_APPS`.
2. Keep `MIGRATION_MODULES["mcp"] = "apps._legacy.mcp_migration_only.migrations"`.

At the next major line (1.x+), those entries can be pruned by the existing legacy-shim drop logic.
