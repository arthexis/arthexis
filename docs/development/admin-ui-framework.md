# Admin UI consistency framework

This project now includes a shared **admin UI framework** for consistent spacing, control sizing, and action layout in custom admin templates.

## Goals

- Ensure buttons and action links render at a predictable, uniform height.
- Use common spacing and panel primitives so custom views feel native.
- Provide a repeatable pattern for future admin customizations.

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
