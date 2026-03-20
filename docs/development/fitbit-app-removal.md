# Fitbit app removal and migration-only compatibility path

The runtime `fitbit` Django app has been removed from automatic discovery and replaced with the explicit legacy migration-only app `apps._legacy.fitbit_migration_only.apps.FitbitMigrationOnlyConfig`.

## Release note for operators

Operators must confirm that every deployed database has already applied `fitbit.0002_remove_fitbit_models` before removing the legacy migration-only app from shipped releases.

If an environment has not applied that migration yet, upgrade first to a release that still ships the migration-only Fitbit app, run `python manage.py migrate`, and only then continue to a later release that fully deletes Fitbit support.

Environments that skip the historical Fitbit migration chain must migrate through that intermediate release before upgrading past the Fitbit removal release.

## Repository baseline

Fresh installs should not discover or enable the historical Fitbit runtime app from `apps/fitbit/`.

The migration-only package remains under `apps/_legacy/fitbit_migration_only/` so long-tail deployments can finish the historical migration chain while the active codebase stays free of Fitbit runtime models, admin registrations, fixtures, and documentation references.
