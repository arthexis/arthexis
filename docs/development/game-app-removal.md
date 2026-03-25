# Game app removal and legacy upgrade path

The `apps.game` package has been removed from the fresh-install baseline.
New environments should **not** include `apps.game` in manifest-driven app loading or `INSTALLED_APPS` discovery.
Upgrade compatibility is now provided by `apps._legacy.game_migration_only`.

## Scope of this cleanup

This repository state assumes the cleanup is applied as part of a fresh-install baseline or migration reset process.
The deleted package included only `apps/game/migrations/0001_initial.py`, and no other app declared inbound migration dependencies on `game.0001_initial` in the current tree.

## Legacy upgrade path for existing installations

Existing installations that already applied `apps.game` migrations can upgrade in place through the migration-only shim:

1. Ensure `apps._legacy.game_migration_only.apps.GameMigrationOnlyConfig` is present in `LEGACY_MIGRATION_APPS`.
2. Ensure `MIGRATION_MODULES["game"]` points to `apps._legacy.game_migration_only.migrations`.
3. Run `python manage.py migrate` and confirm `game.0002_archive_and_drop_avatar` is recorded.

The migration archives `game_avatar` into `game_archived_avatar` so rollback remains available.
