# Recipes

The `apps.recipes` app stores scriptable Python snippets that can be reused across the platform for
custom actions or validation logic. Prefer recipes whenever another app needs to trigger a
configurable script or run a user-defined validation without shipping new code.

## Why use recipes?

- **Reusable actions**: centralize logic that needs to be executed from multiple workflows.
- **Custom validations**: make runtime checks configurable without requiring migrations in other apps.
- **Sigil support**: scripts can reference existing `[SIGILS]` and recipe arguments via `[ARG.*]` tokens.

## Recipe fields

Each recipe includes:

- **Slug**: unique identifier used when calling the recipe via CLI.
- **UUID**: stable natural key for integrations and fixtures.
- **Verbose name**: human-friendly display value.
- **Script**: the Python snippet to execute.
- **Result variable**: the variable name that holds the final result (defaults to `result`).

## Running a recipe from code

```python
from apps.recipes.models import Recipe

recipe = Recipe.objects.get(slug="validate-license")
execution = recipe.execute("premium", region="na")
print(execution.result)
```

The `execute()` method accepts positional and keyword arguments. It resolves `[ARG.*]` sigils,
then resolves all other `[SIGILS]` against the recipe before executing the script.

## Running a recipe from the CLI

Recipes can be run like a Django management command with the existing helpers:

```bash
./do.sh recipe validate-license premium region=na
```

On Windows:

```bat
do.bat recipe validate-license premium region=na
```

Arguments passed after the recipe identifier are made available as:

- **Positional args** → `[ARG.0]`, `[ARG.1]`, ...
- **Key/value args** → `[ARG.KEY]` (use `key=value` or `--key=value` syntax)

## Example recipe script

```python
# Script contents
allowed = "[ARG.0]" == "premium"
region = "[ARG.region]"

result = {
    "allowed": allowed,
    "region": region,
}
```

The recipe result is read from the configured result variable (default `result`) and returned from
`execute()` as `execution.result`.

## Security considerations

**Warning:** Recipes are executed as Python code on the server. Granting users permission to
create or edit recipes is equivalent to giving them shell access. Only highly trusted
administrators should have these permissions.
