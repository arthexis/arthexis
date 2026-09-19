# Arthexis 2.0 transition plan

## Transition decision

Arthexis 2.0 starts as a clean repository at `v2/arthexis`.  It is a focused
reimplementation, not a large-scale deletion pass over 1.x.  The frozen source
record is [FROZEN_1X_SOURCE.md](FROZEN_1X_SOURCE.md).

Only behavior and data that are explicitly selected will enter 2.0.  The old
checkout remains available as a read-only source of contracts and future export
input; it is not an installation dependency or an upgrade target.

The installer detects the configured destination SQLite database. It accepts a
new or 2.0-marked destination and stops safely on a legacy destination.
Reconciliation is explicit: `install.sh --import /path/to/old.sqlite3` reads a
separate source database without changing it after the 2.0 schema is ready.

## Design constraints

* Build only selected 2.0 capabilities; do not port source merely to preserve
  history.
* Keep each new module, app file, and management/script file between roughly
  400 and 800 lines at most. Split distinct responsibilities into packages
  before a file approaches that range; small focused files are preferred.
* Keep the runtime clone-local. Install/start/stop must not create host units,
  system-wide links, or depend on the frozen checkout.
* Use a static app registry. Node roles and optional capability selection are
  product configuration, not dynamic Django app installation.
* Every model change includes a migration, relevant tests, and migration
  validation.
* Legacy data is protected by detection and explicit operator action. The
  versioned importer is read-only at the source; no in-place 1.x upgrade path
  is introduced.

## Current baseline

* Local repository: `/home/arthe/Repos/v2/arthexis`
* Branch: `main`
* Version: `2.0.0.dev0`
* Runtime: Django 5.2, SQLite for the blank foundation
* Installed app: `apps.base`, which owns the schema-generation marker

## Progress

* V200-01 foundation: complete 2026-09-17, pending review of this fresh-repo
  transition boundary.
* V200-02 retained-domain contracts: complete 2026-09-17 in
  `docs/transition/retained-domains.md`.
* V200-03 app-package foundation: complete 2026-09-17. The eight selected apps
  now have static registration, manifests, admin boundaries, fresh initial
  migration boundaries, and focused foundation tests. The first retained
  domain models now cover node topology, logical cards, accounts/ledgers,
  sigil roots, event envelopes, and core OCPP records. The initial runtime
  slice now adds the OCPP 1.6 WebSocket actions for boot, heartbeat,
  authorization, status, and transaction start/stop; structured event
  publication; and an OCPP-only Celery maintenance schedule. Broader protocol
  coverage and remaining domain refinements remain subsequent implementation
  work.
* OCPPX-01: complete 2026-09-17. The frozen registry records 87 OCPP 1.6 and
  2.0.1 version/direction contracts with payload, result, call-error, and
  persistence-owner declarations.
* OCPPX-02: complete 2026-09-17. The version-aware transport now negotiates
  OCPP 1.6/2.0.1, verifies registered charger credentials, validates frames,
  dispatches through the registry, and correlates outbound results, errors,
  timeouts, and disconnects.
* OCPPX-03: complete 2026-09-17. OCPP persistence, administration, and domain
  services are now split into small asset, session, operation, configuration,
  reservation, profile, notification, monitoring, certificate, and status
  modules with fresh 2.0 migrations.
* OCPPX-04: complete 2026-09-17. All retained OCPP 1.6 inbound actions now
  resolve to versioned handlers, and all outbound actions have explicit payload
  validation, live-connection emission, and persisted result/error outcomes;
  no automatic charger-operation scheduling was added.
* OCPPX-05: complete 2026-09-17. All retained OCPP 2.0.1 inbound actions now
  resolve through version-specific session, notification, report, and
  certificate handlers; all outbound actions validate and explicitly emit via
  `emit_v201_operation`, with persisted correlation outcomes and no automated
  charger-operation flow.
* OCPPX-06: complete 2026-09-19 under the model-first GWAY surface in
  `docs/transition/charger-command-surface.md`. The app-wide `charger` report
  remains read-only; `Charger.reset`, `Charger.start`, and `Charger.stop` are
  typed GWAY-pipeline model APIs. Safe operation administration, full inbound
  protocol-client simulation, and correlation outcome coverage are in place.
* OCPPX-07: complete 2026-09-19. The executable matrix report has all 87 frozen
  OCPP 1.6/2.0.1 contracts implemented, simulator/correlation coverage passes,
  and OCPP schedules contain no charger-operation dispatch.
* OCPPX-08: complete 2026-09-19. Authenticated first connection can enroll a charger using a
  configured enrollment-token hash. New chargers default to open authorization;
  an administrator can switch each charger to restricted mode. Both retained
  protocol surfaces enforce the configured policy.
* V200-04 and V200-05: execution is governed by
  [rebuild-execution-plan.md](docs/transition/rebuild-execution-plan.md):
  reconciliation is independent of Django migrations; PR gates are limited to
  four critical flows; Watchtower is evidence-only until GWAY owns approved
  deployment; and cutover is local-first with a verified backup.

## Delivery sequence

### Task V200-01: Freeze source and establish the 2.0 foundation

* Intent: Create an independent, runnable 2.0 base with an unambiguous source
  provenance record.
* Scope: `FROZEN_1X_SOURCE.md`, `VERSION`, `pyproject.toml`, `manage.py`,
  `arthexis/`, `apps/base/`, `install.sh`.
* Constraints: Do not copy legacy apps or create an implicit 1.x dependency.
  Keep the first data model limited to ownership of the schema family.
* Acceptance Criteria: A new data directory migrates cleanly; the database
  contains a generation-2 marker; the admin and Django checks load.
* Verification Commands:
  `.venv/bin/python manage.py check --fail-level ERROR`; `.venv/bin/python
  manage.py makemigrations --check --dry-run`; `.venv/bin/python manage.py
  test tests`.
* Out of Scope: OCPP, Celery, legacy data import, node role behavior, and
  production service deployment.
* Depends on: none.
* Blocks: V200-02, V200-03, V200-04.
* Parallel-safe: no.
* Risk/Rollback: low; primary failure is an unusable blank install. Roll back
  by removing only the newly created clone-local `var/` directory.

### Task V200-02: Retained-domain contract selection

* Intent: Turn the 1.x inventory into explicit 2.0 contracts before importing
  application behavior.
* Scope: `docs/transition/` and narrowly scoped contract tests for each
  approved domain.
* Constraints: Selection is affirmative: unselected 1.x apps and models are
  not ported by default. Preserve administrator capability for selected domains.
* Acceptance Criteria: Each selected domain names its owner app, public/admin
  contract, retained data, excluded data, and test boundary.
* Verification Commands: `.venv/bin/python manage.py test tests`;
  `git diff --check`.
* Out of Scope: Implementing domain models or any legacy import code.
* Depends on: V200-01.
* Blocks: V200-03.
* Parallel-safe: yes.
* Risk/Rollback: medium; primary failure is accidentally importing obsolete
  behavior. Roll back by revising the contract before app scaffolding begins.

### Task V200-03: Rebuild selected apps in small packages

* Intent: Implement approved domains one at a time as 2.0-native Django apps.
* Scope: selected `apps/<name>/` packages, their admin configuration,
  migrations, routes when web-capable, and tests.
* Constraints: Use the suite scaffold command when it exists. Backend-only apps
  include the required `APP_STRUCTURE` marker. Split responsibilities into
  packages before modules grow large; target 400–800 lines per file maximum.
* Acceptance Criteria: Each selected app has a manifest, admin configuration,
  migration history beginning in 2.0, and supported-contract tests.
* Verification Commands: `.venv/bin/python manage.py makemigrations --check
  --dry-run`; `.venv/bin/python manage.py test apps.<name>`; `python -m ruff check
  --config pyproject.toml apps arthexis scripts tests manage.py`; `python -m
  ruff format --check --config pyproject.toml apps arthexis scripts tests
  manage.py`.
* Out of Scope: Compatibility shims and wholesale source-file copying.
* Depends on: V200-02.
* Blocks: V200-04, V200-05.
* Parallel-safe: no, except independently owned approved apps.
* Risk/Rollback: medium; primary failure is coupling to retired 1.x internals.
  Roll back the isolated app/migration before importing additional data.

### Task V200-04: Define data reconciliation after structure stabilizes

* Intent: Specify the narrow, auditable movement of approved 1.x data into the
  completed 2.0 models.
* Scope: a read-only SQLite interchange, dry-run report, explicit mapping
  rules, and a separate reconciliation package invoked by the installer only
  when `--import` names a source.
* Constraints: Do not begin until all destination models for an approved domain
  are stable. Never mutate the 1.x source database. Require explicit source and
  destination paths; redact sensitive values in reports.
* Acceptance Criteria: The reconciler can dry-run a copy, report skipped and
  transformed records, import only selected data, and be rerun safely.
* Verification Commands: `.venv/bin/python manage.py test <reconciler targets>`;
  `.venv/bin/python manage.py makemigrations --check --dry-run`; `git diff --check`.
* Out of Scope: An automatic in-place upgrade, importing every legacy table,
  legacy migration replay, and source-database writes.
* Depends on: V200-03, OCPPX-07, OCPPX-08.
* Blocks: V200-05.
* Parallel-safe: no.
* Risk/Rollback: high; primary failure is incorrect data transformation. Roll
  back by discarding the 2.0 destination database and retaining the untouched
  1.x source and dry-run report.

**Execution checkpoint (2026-09-19):** OCPPX-07 and OCPPX-08 are complete.
The selected source-to-target map, exclusions, validation, and cutover evidence
are recorded in [rebuild-execution-plan.md](docs/transition/rebuild-execution-plan.md).

### Task V200-05: Production lifecycle and deployment boundary

* Intent: Add supported serving, worker, and node integration only for the
  selected completed domains.
* Scope: clone-local lifecycle packages, operator documentation, and required
  deployment configuration.
* Constraints: Keep scripts small and package complex actions by responsibility.
  No host-level units or integration dependencies are introduced without an
  explicit deployment task.
* Acceptance Criteria: The chosen deployment mode starts, stops, and reports
  health without depending on 1.x; operator steps are documented and tested
  where executable.
* Verification Commands: `./install.sh`; relevant lifecycle tests; `.venv/bin/
  python manage.py check --deploy` with deployment configuration supplied.
* Out of Scope: Reintroducing retired host-wide lifecycle behavior.
* Depends on: OCPPX-07, OCPPX-08, V200-04.
* Blocks: none.
* Parallel-safe: no.
* Risk/Rollback: medium; primary failure is deployment coupling. Roll back the
  clone-local deployment configuration without changing legacy services.

## Deferred reconciliation checkpoint

Before V200-04 begins, revisit the retained-domain contract inventory and
approve the exact data sets to move. Until that checkpoint, the correct install
behavior on an existing 1.x database is to stop without modifying it.
