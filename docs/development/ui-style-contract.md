# UI style contract check

The UI style contract validator helps keep admin templates and related stylesheet usage consistent and discoverable.

## Command

Use the unified health-check entrypoint:

- `.venv/bin/python manage.py health --target core.ui_style_contract`

You can also run the script directly:

- `.venv/bin/python scripts/check_ui_style_contract.py`

## What is validated

The checker scans:

- `apps/*/templates/admin/**/*.html`
- Stylesheets referenced by those templates via `{% static '...css' %}` or `href="...css"`

It reports:

1. **Unknown prefixes** (`UNKNOWN_PREFIX`)
   - Class names with prefixes that are not represented in `docs/development/ui-style-index.md`.
2. **Forbidden generic class names** (`FORBIDDEN_GENERIC_CLASS`)
   - Generic class names blocked by policy (for example `container`, `wrapper`, `title`).
3. **Inline style usage without explicit allow marker** (`INLINE_STYLE_DISALLOWED`)
   - `<style>` blocks and `style="..."` attributes are blocked unless the template contains:
     - `admin-ui-framework: allow-custom-css`
4. **New class names not indexed** (`NEW_CLASS_NOT_INDEXED`)
   - Any class not present in `docs/development/ui-style-index.md` unless temporarily waived.

## Temporary waivers

For intentionally short-lived class names, add an explicit waiver comment in the template or stylesheet:

- `ui-style-contract: waive-new-class <class-name>`

Example:

- `ui-style-contract: waive-new-class my-new-temporary-class`

Waivers should be removed after the class is added to `ui-style-index.md` or renamed.

## Failure message format

Each violation is emitted as:

- `<CODE>: <path>:<line>: <message>`

Example:

- `NEW_CLASS_NOT_INDEXED: apps/demo/templates/admin/demo/page.html:12: Class 'foo-bar' is not indexed...`

## Remediation checklist

When the check fails:

1. **For `UNKNOWN_PREFIX`**
   - Rename the class to an existing prefix family, or
   - Add the class to `docs/development/ui-style-index.md` when the new family is intentional.
2. **For `FORBIDDEN_GENERIC_CLASS`**
   - Replace generic names with descriptive, namespaced classes.
3. **For `INLINE_STYLE_DISALLOWED`**
   - Move styles into shared/admin stylesheet assets, or
   - Add `admin-ui-framework: allow-custom-css` only when custom inline CSS is necessary.
4. **For `NEW_CLASS_NOT_INDEXED`**
   - Add the class to `docs/development/ui-style-index.md`, or
   - Add a temporary waiver marker and follow up to remove it.
