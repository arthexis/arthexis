# Recipes app retirement and legacy migration-only path

The runtime `recipes` Django app has been removed from automatic discovery and replaced with the explicit legacy migration-only app `apps._legacy.recipes_migration_only.apps.RecipesMigrationOnlyConfig`.

## Release note for operators

Operators must apply the dependent cleanup migrations that remove live foreign-key usage from `actions`, `emails`, `shortcuts`, and `sensors` before the recipe retirement migration is allowed to move the historical recipe tables aside.

After those dependents are removed, `recipes.0004_retire_recipe_tables` renames the historical tables to `recipes_recipe_retired` and `recipes_recipeproduct_retired`. This keeps upgrade reversibility and preserves data for audit or rollback workflows without leaving the runtime app enabled.

## Fresh-install baseline

Fresh installs still satisfy the historical migration graph through the migration-only compatibility app, but they do not load the former runtime models, admin registrations, commands, or tests.

## Upgrade constraints

Environments that skip the intermediate cleanup release path must not jump directly from a build that still actively uses runtime recipes to a build where the runtime package is absent. Migrate through a release that includes the dependent foreign-key retirement migrations first, run `python manage.py migrate`, and only then move onward.
