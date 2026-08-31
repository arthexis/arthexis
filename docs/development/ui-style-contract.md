# UI style contract

This document is the single source of truth for CSS naming and design-token usage in Arthexis admin and app UI templates.

Use this contract with `docs/development/admin-ui-framework.md` to keep implementation aligned with shared primitives while preserving app flexibility.

## Scope split

### Global admin primitives

Global primitives are shared, reusable classes defined by the admin UI framework and intended for cross-app consistency.

- Prefix: `admin-ui-*`
- Examples: layout containers, spacing stacks, action rows, control styles, muted text helpers.
- Ownership: shared framework CSS and admin base templates.
- Usage rule: prefer these first for common layout and controls before adding app-local classes.

### App-local classes

App-local classes are component classes scoped to one app or feature when a shared primitive does not express the UI intent clearly.

- Prefix family: `ui-*` (component structure), `is-*` (state), `js-*` (behavior hooks).
- Ownership: app CSS/templates.
- Usage rule: app-local classes may extend but should not silently replace framework primitives for common controls.

## Naming rules

Use these naming patterns exactly.

### Shared primitives

- Format: `admin-ui-*`
- Purpose: cross-app layout/control primitives owned by the shared admin UI framework.

### Component block

- Format: `ui-<component>`
- Example: `ui-simulator-card`

### Element

- Format: `ui-<component>__<element>`
- Example: `ui-simulator-card__summary`

### Variant/modifier

- Format: `ui-<component>--<variant>`
- Example: `ui-simulator-card--compact`

### State

- Format: `is-<state>`
- Example: `is-loading`
- Constraint: state classes are state-only and must never be used as styling-only replacement for a proper component block.

### JavaScript hooks

- Format: `js-<behavior>`
- Example: `js-toggle-advanced`
- Constraint: use only for behavior targeting. Never use `js-*` classes to apply visual styling.

## Reserved prefixes and forbidden patterns

### Reserved prefixes

The following prefixes are reserved and mandatory for their intended purpose:

- `admin-ui-`
- `ui-`
- `is-`
- `js-`

Do not invent alternate project-wide prefixes for the same concerns.

### Forbidden patterns

Avoid ad-hoc or ambiguous class names, including:

- Generic utility-like names without semantic intent (for example, `.left`, `.big`).
- Numbered style names that do not communicate purpose (for example, `.button2`).
- Visual-only replacement classes where a state or component class should exist (for example, using `.text-red` instead of `.is-error`).

## Token usage and sizing policy

Do not hardcode `rem` or `px` for control sizing when a design token already exists.

- Prefer framework tokens such as `--admin-ui-control-height` and related spacing/radius tokens from the admin UI framework (for example, `--admin-ui-space-4`, `--admin-ui-radius-sm`).
- Hardcoded lengths are acceptable only when no suitable token exists and the value is truly app-specific.
- If the same hardcoded value appears in multiple places, promote it to a shared token or shared primitive.

This policy is aligned with current AGENTS guidance and the admin UI framework documentation.

## Migration guidance for legacy ad-hoc class names

When migrating legacy templates and CSS:

1. Audit classes in the target template and stylesheet.
2. Map global layout/action/control patterns to `admin-ui-*` primitives first.
3. Rename app-specific structures to the full BEM-style patterns: `ui-<component>`, `ui-<component>__<element>`, and `ui-<component>--<variant>`.
4. Convert visual state toggles to `is-*` classes.
5. Move JavaScript selectors to `js-*` classes where behavior hooks are needed.
6. Replace hardcoded control sizing with existing tokens where available.
7. Remove obsolete legacy classes after template and CSS updates are complete.

Migration goal: reduce style drift while keeping Arthexis extensible through shared primitives plus explicit app-local component structure.
