# Hosted JS extensions app removal

The runtime `extensions` Django app has been removed from automatic discovery and replaced with the legacy migration-only app `apps._legacy.extensions_migration_only.apps.ExtensionsMigrationOnlyConfig`.

## Rationale

Hosted JS extensions allowed arbitrary browser extension code to be stored and distributed from the database. That programmable surface has been retired so Arthexis can focus on framework and service integration through bounded Django apps, models, and migrations.

## Migration behavior

Existing `extensions_jsextension` rows are copied into the archival table `extensions_archivedjsextension` by the reversible migration `extensions.0004_archive_and_drop_jsextension`, then the live `JsExtension` model/table is removed.

Reversing that migration recreates the historical `JsExtension` table and restores archived rows.
