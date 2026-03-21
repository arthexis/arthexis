# Socials app removal and legacy upgrade path

The social/profile runtime features were already removed before this cleanup.
This repository state now completes the retirement by deleting the remaining `socials` migration shim and its preserved migration files.

## Scope of this cleanup

- `apps.socials` is absent from the repository baseline.
- `config.settings.apps.LEGACY_MIGRATION_APPS` no longer exposes a migration-only `socials` app label.
- `config.settings.apps.MIGRATION_MODULES` no longer pins a `socials` migration package.
- No app in the current tree declares an inbound migration dependency on the retired `socials` graph.

## Supported upgrade baseline

Operators must confirm that every supported deployment has already applied `socials.0004_remove_blueskyprofile_discordprofile` before adopting this repository state.

If an environment still needs any `socials` migration, it must first upgrade to an intermediate release that still ships:

1. `apps._legacy.socials_migration_only.apps.SocialsMigrationOnlyConfig`
2. `apps/socials/migrations/`
3. the `MIGRATION_MODULES["socials"]` override

Run `python manage.py migrate` on that intermediate release so the database records `socials.0004_remove_blueskyprofile_discordprofile` as applied.
Only after that step is complete everywhere should the environment move to this fully retired baseline.

## Operator note

Fresh installs and supported in-place upgrades should no longer reference the `socials` app label at runtime or during migration loading.
