# Arthexis 1.0.0 (draft release notes)

Date: 2026-03-27.
Status: draft.

## Release posture

Arthexis `1.0.0` introduces the migration-baseline reset and removes legacy migration-only shims from runtime wiring.

- Install mode: fresh install/import baseline.
- Upgrade mode: no in-place `0.x` -> `1.0.0` upgrade path after baseline reset lands.

## App and migration cleanup

- Runtime keeps only canonical apps from `PROJECT_APPS`, `DJANGO_CORE_APPS`, `THIRD_PARTY_APPS`, and `PROJECT_LOCAL_APPS`.
- Legacy migration-only app configs are removed from runtime loading for major-version `1.x`.
- Legacy migration module redirects are removed for major-version `1.x`.
- Remaining non-default migration module overrides after cleanup are:
  - `django_celery_beat: apps.celery.beat_migrations`
  - `sites: apps.core.sites_migrations`

## Temporary merge freeze notice

Effective immediately (2026-03-27 UTC), migration-affecting pull requests are under temporary merge freeze until the baseline-reset branch is merged.

Scope of freeze:

- any new migration files
- any migration edits in existing files
- any settings changes that alter migration module routing

Exception policy:

- critical production hotfixes may proceed only with explicit maintainer approval and a follow-up baseline reconciliation task.
