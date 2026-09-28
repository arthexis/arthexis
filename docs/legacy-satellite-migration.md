# Legacy satellite migration rehearsal and cutover

This document is the operator procedure and safety contract for moving a deployed
legacy Arthexis satellite to the current schema generation. It complements the
cross-major reconciliation contract in #240 and hands a verified final migration
artifact to #278 for the authority switch.

## Safety invariant

The legacy satellite remains authoritative until #278 performs the explicit
authority switch. Current Arthexis may run beside it as a secondary,
non-authoritative instance.

The migration tooling may read the legacy installation and take a consistent
SQLite online-backup snapshot, but it must never reconcile directly from the
changing live database. Restore, reconciliation, verification, reporting, and
bundle creation operate from the finalized immutable capture.

A failed rehearsal or NO-GO must not modify the authoritative legacy source.

## Operator command

Run the migration tooling from the current Arthexis installation and point it at
the still-running legacy Arthexis installation:

```bash
python scripts/reconcile.py rehearse /path/to/legacy/arthexis
```

The command performs:

1. read-only legacy discovery;
2. consistent SQLite online-backup capture;
3. capture integrity verification and finalization;
4. disposable database-only fixture restore;
5. cross-major reconciliation into a fresh current-generation database;
6. hard resource-safety evaluation;
7. retained-domain verification and historical-gap classification;
8. GO / NO-GO reporting; and
9. immutable GO-bundle creation when every gate passes.

Operators do not manually copy or freeze the database before an ordinary
rehearsal.

### Final cutover run

For the final accepted run use:

```bash
python scripts/reconcile.py rehearse /path/to/legacy/arthexis --cutover
```

The start of this run is the cutover boundary. Until an explicit incremental
final-synchronization mechanism exists, the legacy database must not advance
between its accepted capture and the final no-missed-writes proof.

The current implementation enforces this conservatively: after reconciliation it
re-hashes the live legacy database. If that hash differs from the captured
database, the result is NO-GO with
`legacy-source-advanced-after-capture`. Quiesce legacy writes and rerun rather
than accepting a stale migrated database.

A successful `--cutover` run still does **not** switch production authority.
It creates the exact artifact that #278 may consume.

## Resource safety gates

Rehearsal resource limits are hard gates, not informational warnings. Defaults
are:

- maximum elapsed time: 1800 seconds;
- maximum peak RSS: 512 MiB;
- maximum rehearsal workspace size: 2048 MiB; and
- minimum free disk before capture: 1024 MiB.

They may be overridden with
`--max-elapsed-seconds`, `--max-peak-rss-mib`,
`--max-workspace-mib`, and `--min-free-disk-mib`.

A preflight disk failure stops before capture. A later threshold breach writes
resource evidence and returns NO-GO with reason `resource-limit`. Exceeding a
threshold means stop and reconsider or rework the migration path.

## Historical incompleteness

Real charger and version-0 history may already be incomplete. Reconciliation
must not manufacture missing history merely to make counts look complete.

The report distinguishes evidence-backed historical gaps from migration loss:

- `source-incomplete`: the captured legacy source itself lacks the required
  table, record, or dependency;
- `legacy-v0-gap`: an identified version-0 persistence/schema limitation; and
- unclassified or migration-caused loss.

The first two may produce **GO with warnings** only when concrete source evidence
is present in the report. Missing evidence, unsupported classifications,
destination count mismatches, integrity failures, or migration-caused loss are
NO-GO.

## Result and exit status

The command writes one JSON result to standard output.

A successful result contains `"decision": "GO"` and exits 0. A rejected
migration contains `"decision": "NO-GO"` and exits 2 for expected safety-gate
failures such as resource limits or a changed cutover source.

Do not infer approval from the mere existence of a reconciled database. The
authoritative result is the final decision plus its evidence.

## Artifact layout

With `--output /migration/rehearsal`, the workspace contains artifacts such as:

```text
/migration/rehearsal/
  captures/
    <capture-id>/
      database/legacy.sqlite3
      manifest.json
      checksums.sha256
      FINALIZED
  fixtures/
    <fixture-id>/
      database.sqlite3
      fixture.json
      reconciled.sqlite3
      reconciliation.json
      migration-report.json
      migration-report.txt
  resource-report.json
  cutover-proof.json          # final --cutover run only
  bundles/
    <bundle-id>/
      database/reconciled.sqlite3
      capture/manifest.json
      capture/checksums.sha256
      migration/reconciliation.json
      migration/migration-report.json
      migration/migration-report.txt
      migration/resource-report.json
      migration/cutover-proof.json   # final --cutover run only
      manifest.json
      checksums.sha256
      FINALIZED
```

Capture IDs include sub-second time information so rapid repeated rehearsals do
not collide. Existing captures, fixtures, and GO bundles are never overwritten.

## GO bundle contract

A GO bundle is the immutable handoff object. Its manifest binds together:

- source capture identity and cryptographic digests;
- detected legacy installation/version evidence;
- reconciled current-generation database and digest;
- reconciliation receipt;
- machine-readable verification report;
- human-readable migration summary;
- accepted historical-gap classifications and evidence;
- resource policy and measurements;
- cutover no-missed-writes proof for a final `--cutover` run;
- provenance linking derived artifacts back to the immutable capture; and
- explicit GO status.

The bundle is checksummed and finalized with a `FINALIZED` marker. A bundle
cannot be overwritten.

NO-GO runs do not create an accepted GO bundle.

## Retention and cleanup

Accepted migration evidence is retained indefinitely by default. Do not
automatically prune finalized capture or GO bundles.

If storage must later be reclaimed, cleanup is an explicit operator action.
Prefer deleting disposable fixture/workspace material before deleting the
finalized source capture or accepted GO bundle. Never delete the only recovery
evidence while the migrated installation still depends on it.

## Reruns and failures

Rehearsals are repeatable. A rerun creates new capture and bundle identities and
does not overwrite previous evidence.

Expected failure behavior includes:

- tampered/corrupt capture -> reject before downstream consumption;
- reconciliation exception -> write a failure receipt when possible;
- verification failure -> NO-GO and no GO bundle;
- resource threshold breach -> NO-GO and resource report;
- legacy source advances during final cutover -> NO-GO and cutover proof;
- existing bundle identity -> refuse overwrite.

In all cases the legacy source remains authoritative and is not repaired,
rewritten, or rolled back by the rehearsal tooling.

## Field acceptance for #276

Before #276 is complete, exercise the complete workflow on **two real
charger/satellite installations**.

For each installation retain:

- ordinary rehearsal evidence as useful during preparation;
- the final `--cutover` GO bundle used for the handoff;
- warnings documenting pre-existing charger/version-0 history gaps; and
- any resource-limit or migration failures encountered while proving the path.

The two field runs are acceptance work, not a reason to weaken the migration
gates.

## Handoff to #278

#278 may begin the production authority-switch procedure only from a finalized
GO bundle produced by the final `--cutover` run.

The handoff consists of:

1. the finalized GO bundle directory;
2. the reconciled database inside that bundle;
3. the immutable source capture identity/digests recorded by the bundle;
4. successful retained-domain verification evidence;
5. resource-safety evidence;
6. accepted historical-gap dispositions; and
7. successful no-missed-writes cutover proof.

#278 owns stopping/replacing production legacy services, binding the current
instance to production charger endpoints, switching authority, post-cutover
verification, and rollback.

A GO from #276 means **eligible for operator-controlled cutover**. It never means
that authority has already switched.
