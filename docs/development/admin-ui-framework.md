# Admin UI consistency framework

This project now includes a shared **admin UI framework** for consistent spacing, control sizing, and action layout in custom admin templates.

## Goals

- Ensure buttons and action links render at a predictable, uniform height.
- Use common spacing and panel primitives so custom views feel native.
- Provide a repeatable pattern for future admin customizations.
- Keep admin surfaces focused on developed and tested suite functionality instead of recipe-like workflows that expect administrators to perform programming-style wiring.

## Framework location

- Stylesheet: `apps/core/static/core/admin_ui_framework.css`
- Included globally from: `apps/sites/templates/admin/base_site.html`

## Available primitives

### Layout helpers

- `.admin-ui-panel`: Card-like container with border, padding, and radius.
- `.admin-ui-stack`: Vertical flex stack with consistent spacing.
- `.admin-ui-actions`: Horizontal action row for buttons/links.
- `.admin-ui-muted`: Secondary text color for descriptions.
- `.admin-ui-results`: Consistent list spacing for result/warning sections.

### Controls

- `.admin-ui-button`: Shared base for links, `<button>`, and submit inputs.
- `.admin-ui-button--primary`: Primary action style.
- `.admin-ui-button--secondary`: Secondary action style.

These classes enforce a standard minimum control height using design tokens (`--admin-ui-control-height`) to avoid uneven button sizes.

## Implementation guidance

When creating or updating admin templates:

1. Wrap content in `.admin-ui-panel` when the page represents a focused task.
2. Put top-level content blocks in `.admin-ui-stack`.
3. Use `.admin-ui-actions` for any row that mixes links and buttons.
4. Apply `.admin-ui-button` with a modifier class (for example, `.admin-ui-button--primary` or `.admin-ui-button--secondary`) to *every* clickable action in custom content.
5. Prefer the `--primary`/`--secondary` modifiers over ad-hoc inline styles, since `.admin-ui-button` alone intentionally does not set a filled background.
6. Keep custom CSS local only when the framework lacks a needed primitive.

## Prototype application

The framework has been prototyped in custom admin pages across `nmcli` and a more complex `ocpp` change list:

- `apps/nmcli/templates/admin/nmcli/networkconnection/run_scan.html`
- `apps/nmcli/templates/admin/nmcli/apclient/run_scan.html`
- `apps/ocpp/templates/admin/ocpp/simulator/change_list.html`

These pages now use common panel, stack, action row, and button primitives to eliminate mismatched action control sizing.

## Rollout plan

- Audit all templates under `apps/*/templates/admin/**` for ad-hoc button/link sizing.
- Migrate pages incrementally to framework primitives.
- Introduce new primitives in `admin_ui_framework.css` only when reused by at least two templates.
- Add visual checks (screenshots) for representative admin pages when changing primitives.


## Enforcement for new changes

To prevent style drift in new admin work, regression tests enforce the following policy:

- New admin templates should use shared primitives from `admin_ui_framework.css`.
- New inline `<style>` blocks and template-level stylesheet links are blocked by default.
- A template may opt out only when custom CSS is truly required, by adding an explicit in-template comment marker with rationale:
  - `admin-ui-framework: allow-custom-css`

Current legacy templates with inline CSS are tracked by an explicit allowlist in tests and are intentionally treated as migration debt.

## Migration roadmap for existing apps

Use the staged rollout below when upgrading legacy admin templates:

1. **Audit per app**
   - List templates under `apps/<app>/templates/admin/**` that contain `<style>` blocks or custom stylesheet includes.
2. **Adopt framework primitives first**
   - Replace ad-hoc button/link styling with `.admin-ui-button`, `.admin-ui-actions`, and panel/stack helpers.
3. **Minimize local CSS**
   - Keep only selectors that cannot yet be represented by shared primitives.
4. **Extract reusable patterns**
   - When two or more apps need the same styling pattern, promote it into `admin_ui_framework.css`.
5. **Burn down allowlist entries**
   - Remove migrated templates from the legacy allowlist so regressions are prevented automatically.
6. **Verify visually for high-impact pages**
   - Capture screenshots of representative admin pages when changing shared primitives.
