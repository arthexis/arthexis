# Rebuild execution plan

This is the execution plan for the Arthexis 2.0 rebuild cutover. Arthexis 2 is
installed alongside the legacy checkout and is the migration actor. The legacy
installation remains passive: current Arthexis receives an explicit path to the
old checkout, captures it read-only, and performs all later work on derived
artifacts. There is no in-place upgrade path.

## Side-by-side legacy capture

On the field node, install current Arthexis separately from the live legacy
installation, then point the current instance at the old checkout:

```bash
ARTHEXIS_DATA_DIR=/opt/arthexis-current/var/lib \
    .venv/bin/python scripts/reconcile.py capture /opt/arthexis-legacy
```

The capture command:

- discovers the legacy SQLite database from the explicit source tree;
- refuses non-legacy databases;
- takes a consistent point-in-time SQLite snapshot using the online backup API,
  so the legacy service may remain live;
- copies only explicitly safe build/version metadata;
- fingerprints likely configuration files without exporting their contents,
  because those files may contain credentials or private material;
- writes a versioned `manifest.json`, `checksums.sha256`, and `FINALIZED`
  marker under `ARTHEXIS_DATA_DIR/migration/captures`;
- verifies the finalized bundle before reporting success.

A finalized capture is input evidence. Restore, reconciliation, and verification
must operate on derived copies and never rewrite the capture bundle. Operators
can recheck an artifact at any time with:

```bash
.venv/bin/python scripts/reconcile.py verify \
    /opt/arthexis-current/var/lib/migration/captures/<capture-id>
```

## Database-only restore fixture

Phase 2 deliberately restores only the captured SQLite database. It does not
recreate the legacy checkout, configuration tree, virtual environment, services,
or network behavior.

```bash
.venv/bin/python scripts/reconcile.py restore \
    /opt/arthexis-current/var/lib/migration/captures/<capture-id>
```

The command verifies the capture first, then creates a fresh disposable fixture
under `ARTHEXIS_DATA_DIR/migration/fixtures` containing only:

```text
<fixture-id>/
    database.sqlite3
    fixture.json
```

`fixture.json` records the source capture ID, source manifest/database hashes,
creation time, and the working database hash/integrity at creation. The fixture
database may be modified or discarded by later reconciliation work; the capture
database remains untouched. Re-running restore creates a new fixture identity
and never silently overwrites an existing fixture.

A 2.0 database is created by its own migrations. After this fixture exists,
reconciliation operates on the disposable legacy SQLite working copy rather
than modifying either the live old checkout or the immutable capture.

## Local cross-major reconciliation from a fixture

Phase 3 is local-first. The current Arthexis instance on the satellite consumes
the database-only fixture and produces a separate current-generation database
on that same node. The legacy fixture database itself remains read-only input.
Remote Watchtower reconciliation is deferred unless field measurements show
that local reconciliation is too costly or risks charger stability.

```bash
.venv/bin/python scripts/reconcile.py reconcile-fixture \
    /opt/arthexis-current/var/lib/migration/fixtures/<fixture-id>
```

By default this creates:

```text
<fixture-id>/
    database.sqlite3       # unchanged legacy working source
    fixture.json
    reconciled.sqlite3     # fresh Arthexis 2 destination
    reconciliation.json    # redacted success receipt
```

The workflow verifies the fixture source hash first, creates a fresh Arthexis 2
database with current migrations and the schema-generation marker, runs the
supported #240 reconciliation engine against the fixture source, and verifies
that the destination classifies as generation 2. The source hash is checked
again after reconciliation; any source mutation is a hard failure.

To protect the live satellite, local reconciliation is resource-conscious by
default. Legacy rows are streamed from SQLite in bounded batches instead of
materializing whole tables in Python memory. The CLI defaults to a batch size of
250 rows and, on POSIX systems, increases niceness by 10 so charger-serving
processes retain CPU priority. Both can be tuned for field testing:

```bash
.venv/bin/python scripts/reconcile.py reconcile-fixture \
    /path/to/fixture --batch-size 250 --nice 10
```

The reconciliation receipt records elapsed time and Linux peak RSS so the first
real satellite rehearsal can determine whether local execution is acceptable.
If measurements show excessive resource pressure, the same capture/fixture
contract can later be transported to Watchtower without changing the local
migration semantics.

A destination may be selected explicitly with
`--destination-database /path/to/output.sqlite3`. Existing destinations are
never overwritten. Failed runs leave a concise `reconciliation-error.json`
diagnostic in the fixture workspace so the failure can be inspected before a
fresh rerun.

## Work packages

1. **R200-01 — Reconciliation interchange.** Read legacy SQLite read-only,
   validate it before target writes, import only retained logical records by
   stable identities, and emit a redacted JSON receipt under
   `ARTHEXIS_DATA_DIR`. Never replay 1.x migrations or modify its database.
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
fixture tests, and a clean install/import smoke test. Record the capture path
and SHA-256, local and pushed SHAs, and reconciliation receipt in the handoff.
