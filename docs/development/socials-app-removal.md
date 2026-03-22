# Socials app removal and migration compatibility shim

The social/profile runtime features were already removed before this change.
This update only removes the leftover `apps.socials` maintenance shell from normal app loading while keeping the historical `socials` migration graph importable.

## What changed

- `apps.socials` is no longer installed as a regular local Django app.
- A dedicated legacy shim now provides the `socials` app label through `apps/_legacy/socials_migration_only/`.
- The shim reuses the preserved migration chain in `apps/socials/migrations/` so older installations can still traverse historical dependencies.
- Runtime application metadata no longer advertises `socials` as an active site app.

## Operator note

No live socials/profile models remain after `socials.0004_remove_blueskyprofile_discordprofile`, so this change should not alter runtime behavior on current deployments.
It only trims the final migration-only shell that had still been loaded like a normal app.
