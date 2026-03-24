# Playwright migration notes

## Selenium namespace removal

The legacy compatibility imports from `apps.selenium` have been removed.

### What changed

- Replace `apps.selenium.models` imports with `apps.playwright.models`.
- Replace `apps.selenium.admin` imports with `apps.playwright.admin`.
- Replace `apps.selenium.playwright` imports with `apps.playwright.playwright`.
- `apps.selenium` is no longer auto-discovered as a local app.

### Upgrade guidance

For migration graph safety, this release still keeps the `selenium` app label available through the `apps._legacy.selenium_migration_only` shim while migrations continue loading from `apps.selenium.migrations`, so existing environments can satisfy dependencies involving:

- `apps/playwright/migrations/0002_migrate_from_selenium.py`
- `apps/selenium/migrations/0009_state_only_remove_legacy_models.py`

Do not remove the selenium migration-only shim until all environments have applied both migrations.
