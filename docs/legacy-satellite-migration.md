# Legacy satellite migration safety contract

This document defines the field-safety contract for moving a deployed legacy
Arthexis satellite to the current schema generation. It complements the
cross-major reconciliation contract in #240.

## Safety rule

The field satellite is authoritative. Never use the field node as the first
place where a cross-major migration is attempted. Capture it, preserve the
capture unchanged, rehearse migration off-node, verify the result, and only
then decide whether the field node may be touched.

## Capture

Before changing the satellite, record a migration bundle containing:

- durable Arthexis database/state required by retained resources;
- source Arthexis version, commit/schema generation when available;
- node and charger identity needed to interpret the state;
- relevant non-secret configuration and deployment metadata;
- timestamps and enough provenance to identify the capture; and
- a digest of every captured artifact.

Secrets, private keys, broker credentials, and physical card-sector keys are
not migration payload. Record that such configuration must be reprovisioned
when necessary rather than copying secrets into the reconciliation artifact.

The original capture is immutable. Rehearsal and conversion operate on copies.

## Off-node rehearsal

Restore a working copy of the capture into an isolated migration fixture. The
fixture must not connect to or command the production charger and must not
publish production events.

Run the supported cross-major export/reconciliation/import path from #240
against the fixture. Do not treat direct cross-major Django migration,
`dumpdata`/`loaddata`, or modification of the source snapshot as an
acceptable substitute.

Retain the reconciliation artifact and machine-readable verification evidence
alongside a human-readable migration report.

## Verification

The report must identify the source, target generation, reconciliation result,
warnings, and dispositions. Verification must cover the retained invariants
defined by #240, including where applicable:

- stable node and charger identities and topology;
- authorization/card/account relationships;
- charging transactions and their retained attribution;
- meter-value/transaction relationships;
- uniqueness and referential integrity;
- resource counts/digests and explicit skipped/discarded records; and
- domain totals for which #240 defines meaningful verification.

Warnings caused by incomplete historical information must be explicit. Missing
data must never be silently invented.

## Reset and incomplete-history handling

A charger or satellite reset is evidence of a discontinuity, not permission to
fabricate continuity. Record the known boundary and classify affected
relationships as verified, recoverable with an explicit transformation, or
unverifiable.

If an invariant required for safe reconciliation cannot be established, the
migration is a no-go until an operator resolves or explicitly redesigns the
reconciliation rule.

## Go / no-go gate

A field cutover is **GO** only when all of the following are true:

1. the immutable source capture and its digests are retained;
2. an isolated restore can be reproduced;
3. the supported reconciliation path completes without unresolved failures;
4. all required retained-domain invariants verify;
5. every material warning or historical discontinuity is documented and has an
   accepted disposition;
6. the report identifies the exact source and target versions/generations; and
7. rollback can restore the original captured state or replace the node without
   destroying the authoritative source evidence.

The result is **NO-GO** when any required condition is absent, verification is
ambiguous, reconciliation fails, source provenance is uncertain, or rollback
evidence is insufficient. A no-go leaves the field node unchanged.

## Field cutover boundary

Approval of a rehearsal authorizes a deliberate field-upgrade procedure; it
does not itself perform the upgrade. The manual upgrade, post-cutover
verification, and rollback procedure belongs to the corresponding field
upgrade work (#278).

After cutover, retain the source capture, reconciliation artifact, report, and
verification evidence as an auditable migration record.
