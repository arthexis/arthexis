# Sponsors app retirement and migration compatibility shim

The runtime `sponsors` Django app has been removed from automatic discovery and replaced with the legacy migration-only app `apps._legacy.sponsors_migration_only.apps.SponsorsMigrationOnlyConfig`.

## What changed

- `apps.sponsors` is no longer installed as a normal runtime app or exposed through manifest-driven app loading.
- Public registration URLs, views, forms, services, admin wiring, and renewal scheduling have been removed.
- The legacy shim reuses the preserved migration chain in `apps/sponsors/migrations/` so existing databases can still resolve historical dependencies during upgrades.
- Application registry cleanup removes stale `apps.sponsors` or `sponsors` rows from the site app catalog.

## Operator note

This repository change cannot inspect your production database directly. Before upgrading, verify whether any retained sponsor history still matters operationally, for example by checking whether the `sponsors_sponsorship`, `sponsors_sponsorshippayment`, or `sponsors_sponsortier` tables still contain rows in your environment.

Current releases keep the historical migration path available instead of dropping those tables blindly. A later cleanup release can archive or remove the tables after operators confirm that no production workflows still rely on the data.
