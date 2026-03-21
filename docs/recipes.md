# Recipes

The `apps.recipes` app represents a legacy script-execution surface that still exists in the codebase, but it does **not** match the suite direction for new work.

## Project direction

- Prefer developed and tested functionality shipped as part of the suite over recipe-like behaviors that force administrators, staff, or users to become programmers.
- Prefer first-class apps, models, migrations, and guided UI flows when integrating with outside systems.
- Continue supporting **SIGILS** for field defaults and basic templating without control flow logic.

## Status of this app

Recipes execute server-side code and therefore remain a high-trust administrative feature. They should be treated as migration debt to be reduced over time, not as a preferred extension mechanism for future capabilities.

## Legacy API and usage reference

The feature is still active in the product for supported workflows and maintenance tasks. Use the references below when you need to operate an existing recipe-backed integration while planning a migration into first-class suite functionality.

### Recipe fields

Each recipe includes:

- **Slug**: unique identifier used when calling the recipe via CLI.
- **UUID**: stable natural key for integrations and fixtures.
- **Verbose name**: human-friendly display value.
- **Script**: the Python snippet to execute.
- **Result variable**: the variable name that holds the final result.

### Running a recipe from code

```python
from apps.recipes.models import Recipe

recipe = Recipe.objects.get(slug="validate-license")
execution = recipe.execute("premium", region="na")
print(execution.result)
```

The `execute()` method accepts positional and keyword arguments. It resolves `[ARG.*]` placeholders first, then resolves all other `[SIGILS]` before executing the script.

### Running a recipe from the CLI

Recipes can still be run with the shipped management command:

```bash
./command.sh recipe validate-license premium region=na
```

On Windows:

```bat
command.bat recipe validate-license premium region=na
```

Arguments passed after the recipe identifier are made available as:

- **Positional args** → `[ARG.0]`, `[ARG.1]`, ...
- **Key/value args** → `[ARG.KEY]` using `key=value` syntax

### `[ARG.*]` placeholders in scripts and templates

`[ARG.*]` placeholders remain supported for recipe-backed workflows, including existing scripts and shortcut output templates.

```python
allowed = "[ARG.0]" == "premium"
region = "[ARG.region]"

result = {
    "allowed": allowed,
    "region": region,
}
```

The recipe result is read from the configured result variable and returned as `execution.result`.

## Security considerations

**Warning:** Recipes are executed as Python code on the server. Granting users permission to create or edit recipes is equivalent to giving them shell access. Only highly trusted administrators should have these permissions.
