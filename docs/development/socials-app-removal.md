# Socials app removal

The social/profile runtime features were already removed before this change.

This update only removes the leftover `apps.socials` maintenance shell from normal Django app loading while preserving historical migration compatibility through `apps._legacy.socials_migration_only.apps.SocialsMigrationOnlyConfig`.

Fresh installs and active runtime app discovery no longer include a `socials` app, and no active URLs, admin registrations, models, content types, or permissions should depend on it.
