# Socials app cleanup

The runtime `socials` Django app has been removed from automatic discovery and replaced with the explicit legacy migration-only app `apps._legacy.socials_migration_only.apps.SocialsMigrationOnlyConfig`.

This does **not** remove any active user-facing social/profile feature, because those features were already deleted before this cleanup. The remaining `apps.socials` package only existed as a maintenance shell that kept the historical migration chain importable.

## What changed

- `config/settings/apps.py` no longer installs `apps.socials` as a normal local app.
- Historical migrations now live under `apps/_legacy/socials_migration_only/` with the preserved Django app label `socials` so older dependency edges continue to resolve.
- Runtime references that suggested `socials` was still an active app were removed from manifests and app-description listings.

## Why the legacy shim remains

Older deployments may still need to traverse the original `socials` migration chain. Keeping the migration-only shim allows Django to resolve those historical nodes without reintroducing runtime models, admin registrations, routes, or other active app behavior.
