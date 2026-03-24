# Archived: Sponsors domain runtime removal

This document is retained strictly for migration compatibility history.
The `sponsors` domain is archived and has no active product/runtime surface.

## Archived status

- Runtime models, admin, views, routes, and menu exposure are retired.
- `sponsors` remains available only as a legacy migration label via `apps._legacy.sponsors_migration_only.apps.SponsorsMigrationOnlyConfig`.
- Migration loading is routed through `apps._legacy.sponsors_migration_only.migrations`, which delegates to the preserved historical chain in `apps/sponsors/migrations/`.

## Operator note

Historical sponsor tables are intentionally preserved by the reversible migration chain and can be retired later through planned data archival.
