# Fitbit app removal and closed migration compatibility window

The temporary `fitbit` migration-only shim is no longer shipped. Current releases now assume every supported database has already applied `fitbit.0002_remove_fitbit_models` on an earlier release.

## Release and deployment expectation

There is no longer a supported upgrade path in this release line that stops before `fitbit.0002_remove_fitbit_models`. Databases that have not applied that migration must first upgrade through an earlier release that still shipped `apps._legacy.fitbit_migration_only`, run `python manage.py migrate`, and only then continue to current releases.

In other words, the compatibility window is closed for this branch: if a deployment skipped the historical Fitbit cleanup migration, it must step through that earlier release before adopting any build that deletes the shim package.

## Repository baseline

Fresh installs should migrate cleanly without any `fitbit` app present in `INSTALLED_APPS`, and the migration graph should contain no remaining dependencies on the `fitbit` app label.

If a future audit finds an environment that still requires the Fitbit migration chain, solve that by upgrading through the preserved earlier release rather than restoring a disconnected side path in the current codebase.
