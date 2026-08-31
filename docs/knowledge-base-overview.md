# Knowledge Base Overview

This page maps the current Arthexis documentation surfaces and records the most
obvious knowledge-base gaps visible from the repository tree. Use it as a
starting point when deciding where a new guide belongs or what to repair next.

## Documentation Surfaces

| Surface | Purpose | Canonical entry point |
|---|---|---|
| Repository overview | Product purpose, role architecture, install entry points, and broad feature claims. | `README.md` |
| Documentation index | Human-facing map for the maintained docs tree. | `docs/index.md` |
| Operators manual | Curated field handbook generated as one Kindle-readable text file. | `docs/operators-manual.md`, `docs/operators-manual.json` |
| Suite docs bundle | Full generated bundle containing `README.md`, supported `docs/` files, and supported `apps/docs/` files. | `docs/services/kindle-postbox.md` |
| Operational runbooks | Repeated procedures, incident response, host/service operation, and field recovery. | `docs/operations/` |
| Developer references | Architecture, CI, release, testing, app structure, and implementation policies. | `docs/development/` |
| Integration references | Endpoint inventories, onboarding tracks, token lifecycle, and external-system contracts. | `docs/integrations/` |
| Service references | Runtime services, timers, hardware-facing daemons, and local operational boundaries. | `docs/services/` |
| Cookbooks | Task-oriented admin and operator recipes maintained with the suite app. | `apps/docs/cookbooks/` |
| Model documentation links | Database-backed links from Django model metadata to documentation paths. | `apps/docs/models.py` |

## Current Strengths

- Install, start, stop, upgrade, auto-upgrade, and startup ordering have
  dedicated references and are included in the operators manual.
- Control-node hardware has first-pass coverage for LCD, RFID scanner, USB
  inventory, Kindle postbox, and USB camera power-off.
- Core service operation has separate pages for suite service, Celery worker,
  Celery beat, LCD, RFID scanner, and LLM LCD summary.
- OCPP and charger-facing work has stronger coverage than most other endpoint
  surfaces, including protocol manuals, status mapping, and endpoint inventory.
- Documentation governance exists and defines canonical locations, archive
  rules, and a lightweight review checklist.
- Cookbook QA guidance exists for link/path integrity, workflow accuracy,
  security, currency, and ownership.
- Kindle Postbox provides both a curated operators manual and a full suite
  documentation bundle, giving field operators an offline handoff path.

## Obvious Gaps

### Navigation Integrity

The initial audit found that `mkdocs.yml` had referenced several files that were
not present in the tree:

- `docs/feature/index.md`
- `docs/feature/token-management.md`
- `docs/feature/llm-summary.md`
- `docs/energy-transactions-ledger.md`
- `docs/recipes.md`
- `docs/release-notes.md`
- `docs/development/dual-major-migrations.md`
- `docs/legal/THIRD_PARTY_LICENSES.md`

The stale entries should stay removed unless current replacement pages are added.
A docs CI check should catch this class of drift before it lands.

### Endpoint Documentation Coverage

`docs/integrations/documentation-completeness-checklist.md` already shows the
largest contract gaps. The highest-value next passes are:

- Complete partially documented surfaces for `apps.nodes`, passkeys,
  `apps.cards`, and `apps.odoo`.
- Add first-pass contract documentation for the many "not yet documented"
  route groups, especially `apps.core` and `apps.docs`,
  `apps.features`, `apps.ops`, `apps.repos`, and `apps.clocks`.
- Require new endpoint PRs to update the completeness checklist or explain why
  no public contract changed.

### Audience Paths

The top-level index is useful but mostly flat. New operators, field technicians,
Watchtower maintainers, and app developers need clearer "start here" paths that
connect the existing pages into role-specific sequences.

Suggested paths:

- Control-node operator: install, service status, local hardware, logs, error
  reports, recovery.
- Charger operator: OCPP status, charger actions, token/RFID workflows, cutover
  runbooks.
- Developer: app structure, dependency management, testing, CI
  troubleshooting, and release process.
- Documentation maintainer: governance, cookbook QA, Kindle Postbox, operators
  manual manifest, endpoint checklist.

### Generated Bundle Ownership

The full Kindle Postbox bundle includes broad documentation roots, while the
operators manual uses a curated manifest. This is a good split, but new broadly
useful docs can still miss the field handbook unless `docs/operators-manual.json`
is updated intentionally.

The manifest should remain curated, but documentation review should ask:

- Is this operationally useful in the field handbook?
- Is the fact stored in the owning canonical page rather than duplicated?
- Did the generated manual build fail fast if a source moved or disappeared?

### Cookbook And Docs Tree Relationship

Cookbooks live under `apps/docs/cookbooks/`, while most durable references live
under `docs/`. Several cookbooks are linked from `README.md` or `docs/index.md`,
but there is no concise index that states which cookbooks are current, which are
role-specific, and which should be retired or folded into canonical docs.

### Model And Admin Workflow Coverage

The docs app can link Django models to documentation, but the current knowledge
base does not provide an obvious audit view showing which high-use admin models
have linked operator documentation. This makes admin workflow gaps harder to see
than endpoint gaps.

### Documentation Validation

Existing docs tests cover Kindle bundle behavior and there is a script for HTTP
link policy, but the visible tree still has stale MkDocs navigation references.
Add a fast documentation validation target that checks:

- MkDocs navigation paths exist.
- `docs/index.md` links to local docs resolve.
- `docs/operators-manual.json` sources exist and build.
- Cookbook maintainer links resolve.

## Suggested Next Documentation PRs

1. Fix MkDocs navigation drift and add a CI check for missing nav targets.
2. Add role-based "start here" paths to `docs/index.md`.
3. Expand endpoint contracts for `apps.core` and `apps.docs`.
4. Add a cookbook index with status, owner, audience, and canonical-doc links.
5. Add a model/admin documentation coverage report or management command.
