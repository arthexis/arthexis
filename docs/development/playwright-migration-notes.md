# Playwright migration notes

## Selenium runtime retirement

The `apps.selenium` runtime Django app has been retired. Arthexis now keeps Selenium available only through the legacy migration-only shim `apps._legacy.selenium_migration_only.apps.SeleniumMigrationOnlyConfig`.

### What changed

- Replace `apps.selenium.models` imports with `apps.playwright.models`.
- Replace `apps.selenium.admin` imports with `apps.playwright.admin`.
- Replace `apps.selenium.playwright` imports with `apps.playwright.playwright`.
- The `selenium` app label is preserved only so historical migrations can still resolve `apps.get_model("selenium", ...)` lookups.
- `config.settings.apps.MIGRATION_MODULES["selenium"]` keeps the migration module path pinned to `apps.selenium.migrations` so historical dependencies remain stable.

### Upgrade guidance

The legacy shim exists only to support migration graph resolution for:

- `apps/playwright/migrations/0002_migrate_from_selenium.py`
- `apps/selenium/migrations/0009_state_only_remove_legacy_models.py`

Fresh installs can still migrate cleanly because the shim provides the `selenium` app label during migration loading while leaving runtime models, admin registration, and helper APIs removed.
