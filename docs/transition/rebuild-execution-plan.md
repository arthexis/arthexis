# Rebuild execution plan

This is the execution plan for the Arthexis 2.0 rebuild cutover. A 2.0 database
is created by its own migrations, then an operator may reconcile an explicitly
selected 1.x SQLite database with `install.sh --import /path/to/old.sqlite3`.

## Work packages

1. **R200-01 — Reconciliation interchange.** Read legacy SQLite read-only,
   validate it before target writes, import only retained logical records by
   stable identities, and emit a redacted JSON receipt under
   `ARTHEXIS_DATA_DIR`. Never replay 1.x migrations or copy its database.
2. **R200-02 — Testable boundaries.** Mirror reconciliation code and tests in
   `arthexis/reconciliation` and `tests/reconciliation`; split source before
   it becomes a catch-all compatibility module.
3. **R200-03 — Four critical PR flows.** Port only Clean Install, Python
   Compatibility, Python Quality, and Secret Scan as required gates for
   Python 3.11 and 3.13.
4. **R200-04 — Watchtower preparation.** Retain manual deploy attestation and
   recovery diagnostics. They create evidence; they do not install services,
   expose DNS/TLS, or take over GWAY deployment mechanics.
5. **R200-05 — Local-first cutover.** Bundle and hash the original checkout,
   create an orphan `arthexis-rebuild` branch from this source, commit locally,
   then push it with the approved Bastion credential. No merge or deployment
   occurs as part of cutover.

## Reconciliation scope

The interchange preserves 1.x logical records with 2.0 counterparts: card
credentials and authorization attempts; tariffs, accounts, and ledger entries;
nodes and links; sigil roots; station models, chargers, connectors, profiles,
reservations, transactions, meter values, configuration variables, certificate
metadata, notification/monitoring history, and operational status. It imports
only explicitly mapped columns and skips unknown legacy tables and fields.

The importer never exports record payloads, RFID values, account details,
credentials, private material, GWAY configuration, scanner state, or live
connection channels. A dry run has no target writes.

## Exit evidence

Run Django/migration checks, full tests, Ruff, the OCPP matrix, reconciliation
fixture tests, and a clean install/import smoke test. Record the backup path
and SHA-256, local and pushed SHAs, and reconciliation receipt in the handoff.
