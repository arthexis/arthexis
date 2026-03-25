# Archived: Socials domain runtime removal

This document is retained strictly for migration compatibility history.
The `socials` domain is archived and has no active product/runtime surface.

## Archived status

- Runtime models, admin, views, routes, and menu exposure are retired.
- `socials` remains available only as a legacy migration label via `apps._legacy.socials_migration_only.apps.SocialsMigrationOnlyConfig`.
- Migration loading is routed directly through `apps._legacy.socials_migration_only.migrations`.

## Operator note

No live socials/profile models remain after `socials.0004_remove_blueskyprofile_discordprofile`.
