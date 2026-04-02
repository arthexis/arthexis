# Documentation governance policy

This policy defines where documentation belongs in the Arthexis suite and how to keep docs current without accumulating stale duplicates.

## Document types and canonical locations

- **Quickstart**
  - Purpose: fast setup and first successful run for contributors or operators.
  - Location: top-level `docs/` guides (for example install/start entry docs linked from `docs/index.md`).
- **Operator runbooks**
  - Purpose: production/operations procedures, incident handling, and repeatable admin tasks.
  - Location: `docs/operations/`.
- **Developer internals**
  - Purpose: architecture notes, development workflows, CI/testing conventions, migration strategy, and internal implementation guidance.
  - Location: `docs/development/`.
- **Cookbook / how-to guides**
  - Purpose: task-oriented recipes for a specific outcome without duplicating full reference docs.
  - Location: the closest domain folder in `docs/` (commonly `docs/development/`, `docs/operations/`, or `docs/integrations/`) and linked from `docs/index.md` when broadly useful.
- **Legal**
  - Purpose: license, policy, terms, and compliance-oriented documentation.
  - Location: `docs/legal/` (create this folder when adding new legal documents).

## Update in place vs archive

When changing existing behavior, prefer updating the existing canonical document in place.

Archive content instead of updating in place when one or more of the following is true:

- the old document is retained mainly for historical audits,
- a versioned operational process is intentionally frozen,
- replacing the content inline would remove context needed for upgrades or incident postmortems.

Archive guidance:

- move old content into an `archive/` folder within the same doc domain when practical,
- add a short status note at the top of the archived file with replacement links,
- keep the active document focused on current behavior only.

## Lightweight documentation PR review checklist

- [ ] The doc is in the correct canonical location for its type.
- [ ] Existing canonical docs were updated in place when applicable (no unnecessary duplicates).
- [ ] Any archived doc includes a clear status note and replacement link.
- [ ] Commands, paths, and examples were sanity-checked.
- [ ] Cross-links were updated (`docs/index.md`, `CONTRIBUTING.md`, or relevant local indexes).
