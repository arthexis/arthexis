# Recipes

The `apps.recipes` app represents a legacy script-execution surface that still exists in the codebase, but it does **not** match the suite direction for new work.

## Project direction

- Prefer developed and tested functionality shipped as part of the suite over recipe-like behaviors that force administrators, staff, or users to become programmers.
- Prefer first-class apps, models, migrations, and guided UI flows when integrating with outside systems.
- Continue supporting **SIGILS** for field defaults and basic templating without control flow logic.

## Status of this app

Recipes execute server-side code and therefore remain a high-trust administrative feature. They should be treated as migration debt to be reduced over time, not as a preferred extension mechanism for future capabilities.

## Security considerations

**Warning:** Recipes are executed as Python code on the server. Granting users permission to create or edit recipes is equivalent to giving them shell access. Only highly trusted administrators should have these permissions.
