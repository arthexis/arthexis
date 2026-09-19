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
invocation, then run broader tests when practical. Keep tests under matching
`tests/<area>` packages and split modules before they become compatibility
catch-alls. Never print secrets, raw RFID identifiers, enrollment tokens,
private certificate material, or database payloads in logs, fixtures, or notes.

Arthexis 2.0 migrations create only fresh 2.0 schema. Legacy data enters only
through the explicit read-only reconciliation importer; never copy a legacy
database or replay its migration graph. Reconciliation is idempotent,
auditable, and safe to dry run. Exclude GWAY configuration, physical scanner
state, live connection channels, user credentials, and private keys.

Before any repository cutover, create a verified local backup and complete
local validation. Do not use CI status as proof unless asked. The orphan
`arthexis-rebuild` branch is an integration handoff, not authorization to merge
or deploy it.
