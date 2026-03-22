# Recipes

The old `apps.recipes` runtime has been decommissioned. Arthexis now treats recipe rows as migration-only legacy data that must be replaced with first-class app behavior before the legacy tables are dropped.

## Why recipes were removed

- Executable code stored in database rows turned routine administration into ad-hoc programming.
- The recipe admin and `recipe` management command exposed a server-side code execution surface that did not match the suite direction.
- The remaining supported behaviors now have clearer homes in typed models, management commands, and operator runbooks.

## Inventory and classification

### Fixture inventory

No current fixture file ships `recipes.recipe` or `recipes.recipeproduct` records.

### Production-facing references reviewed during decommissioning

| Reference | Classification | Replacement |
| --- | --- | --- |
| Shortcut clipboard and output-template behavior | 1. First-class Django app/model workflow | `apps.shortcuts` typed targets and template rendering now own this workflow. |
| `recipe` CLI command examples such as `validate-license` | 2. Management command or operator runbook | Replace with purpose-built commands in the owning app; do not reintroduce a generic executable-row runner. |
| Direct `Recipe.objects.get(...).execute(...)` examples | 3. Obsolete behavior that can be archived | Remove from application code and treat as historical migration debt only. |

## Replacement guidance

### 1. First-class Django app/model workflow

Use the owning app's typed model and runtime instead of a database row that stores executable code.

Example: shortcut behaviors now belong in `apps.shortcuts`, where administrators select structured target kinds, identifiers, and payload data. `[ARG.*]` placeholders remain supported for shortcut output templates, but the executable behavior lives in code reviewed with the rest of the app.

### 2. Management command or operator runbook

When an operational task still matters, add a dedicated management command under the app that owns the data and side effects. Document the invocation in that app's operator docs or runbook. Generic recipe execution is intentionally gone.

### 3. Obsolete behavior

If a legacy recipe does not map cleanly to a maintained workflow, archive the script in change history or an operator note and do not migrate it back into the product.

## Database upgrade compatibility

Existing databases can still upgrade through the legacy `recipes` migration path. The migration-only compatibility app preserves historical migrations long enough to:

1. satisfy old migration dependencies,
2. let typed replacements in other apps run, and
3. drop the obsolete recipe tables once downstream migrations no longer rely on them.

This compatibility path is for schema upgrades only. It does **not** restore admin screens, runtime models, or the legacy CLI command.
