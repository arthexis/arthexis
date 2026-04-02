# Cookbook QA checklist

Use this checklist for every cookbook review cycle so Arthexis documentation stays accurate, actionable, and aligned with the current suite architecture.

## When to run this checklist

- Before publishing a new cookbook.
- During release readiness reviews.
- Any time a cookbook references moved code paths, retired apps, or changed admin workflows.

## Review criteria

### 1. Link and path integrity

- Confirm every repository path points to an existing file under `apps/`, `docs/`, or other active project roots.
- Confirm Markdown links resolve to active routes or documents.
- Prefer links to maintained apps and commands over archived or retired runtime surfaces.

### 2. Workflow accuracy

- Re-run the operational steps in the cookbook where feasible.
- Verify command examples match current entrypoints (`./env-refresh.sh`, `.venv/bin/python manage.py ...`, and active lifecycle scripts).
- Ensure steps represent supported Arthexis workflows rather than disconnected side systems.

### 3. Suite-integration coverage

- Check that integrations are described as first-class Arthexis suite components (apps, models, migrations, admin workflows, and commands) when applicable.
- Replace ad-hoc instructions with references to the owning app and documented interfaces.
- Confirm OCPP-facing guidance still reflects Arthexis as the central WebSocket server and integration pivot.

### 4. Security and admin ergonomics

- Remove or correct instructions that encourage unsafe defaults, especially around secrets or privileged operations.
- Keep admin capabilities flexible and explicit; avoid unnecessary restrictions unless a clear security risk exists.
- Verify audit- and traceability-related guidance still maps to current logs, models, or admin histories.

### 5. Currency and ownership

- Flag outdated sections with concrete follow-up tasks and owners.
- Archive or replace cookbooks that no longer map to supported product behavior.
- Record major audit findings in the related PR so future maintainers can follow the decision trail.

## Audit output template

Use this structure in PR descriptions or issue comments when reviewing a cookbook:

- **Cookbook:** `<title>` (`apps/docs/cookbooks/<file>.md`)
- **Status:** `Pass` | `Needs follow-up` | `Archive recommended`
- **Findings:**
  - `<finding 1>`
  - `<finding 2>`
- **Actions:**
  - `<owner> - <task> - <target date>`

## Maintainer references

- [Cookbook maintainer guide](../../../docs/development/cookbook-maintainer-guide.md)
- [Arthexis documentation index](../../../docs/index.md)
