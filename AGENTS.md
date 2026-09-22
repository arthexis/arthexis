# Arthexis 2.0 agent guide

This checkout is the Arthexis 2.0 rebuild. Treat current source, migrations,
and direct local execution as authoritative; do not revive 1.x command
surfaces, migrations, services, or deployment scripts merely because they
exist elsewhere.

Arthexis owns business/domain behavior. GWAY owns host provisioning,
supervision, networking, TLS/DNS, and hardware integration. GWAY may ingest
Arthexis Django models directly. Public model operations need a clear signature
and concise docstring; commands are for application-wide operations, not model
API aliases.

Validate the actual checkout: edit, run a focused test or direct GWAY/Django
invocation, then run broader tests when practical. Tests for first-party Python
packages mirror the production import tree beneath `tests/`, including the
`tests/apps/` level. Use `tests/integration/` only for deliberately
cross-package behavior and `tests/deploy/` for deployment, workflow, recipe,
or service-transition behavior. Keep helpers at the narrowest useful common
package and split broad modules before they become compatibility catch-alls.
Repository-wide tests at `tests/` root are exceptional and guarded by the architecture test. External OCPP conformance/spec tooling lives in `tests/ocpp/`, not in a parallel protocol implementation tree. See `docs/testing.md` for the complete test-topology rules.

Never print secrets, raw RFID identifiers, enrollment tokens, private
certificate material, or database payloads in logs, fixtures, or notes.

Arthexis 2.0 migrations create only fresh 2.0 schema. Legacy data enters only
through the explicit read-only reconciliation importer; never copy a legacy
database or replay its migration graph. Reconciliation is idempotent,
auditable, and safe to dry run. Exclude GWAY configuration, physical scanner
state, live connection channels, user credentials, and private keys.

Before any repository cutover, create a verified local backup and complete
local validation. Do not use CI status as proof unless asked. The orphan
`arthexis-rebuild` branch is an integration handoff, not authorization to merge
or deploy it.
