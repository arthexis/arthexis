# Versioning and Maturity Policy

This policy defines how Arthexis versions are advanced during the release procedure.

Arthexis versions follow `MAJOR.MINOR.PATCH`.

The version number describes both application maturity and database compatibility. The central rule is:

> Django migration compatibility is guaranteed within a major release line. A new major release starts a new schema generation and is reached through reconciliation, not by replaying the new major's Django migration graph against the previous major's database.

## Version increment rules

### MAJOR

A MAJOR increment is a deliberate **schema-generation boundary**.

Increment MAJOR when Arthexis intentionally starts a new Django migration lineage and requires persistent business/domain data to cross the boundary through the supported reconciliation process.

A MAJOR release may use the boundary to:

- replace accumulated migration history with fresh initial migrations
- remove migration-only compatibility shells for retired applications
- rename, split, merge, or reorganize models without preserving the old migration graph
- redesign database structures around the current business domain
- permanently remove obsolete infrastructure-oriented applications

Application deletion, API breakage, or a large refactor does **not by itself** require a MAJOR bump. Those changes may occur inside the current major line when its migration graph can still upgrade supported databases in that line.

Because a MAJOR boundary changes the database compatibility contract, it must be selected deliberately by maintainers. Automatic release-impact detection must not infer a MAJOR bump merely from deleted apps or changed public interfaces.

When MAJOR increments, MINOR and PATCH reset to `0`.

### MINOR

Increment MINOR for meaningful application/domain contract changes that remain inside the current schema generation, including:

- adding or removing an app from the supported runtime surface
- public-facing views, forms, or fields that affect cross-app behavior
- public APIs used across app boundaries
- creating or deleting models within an app
- changes to public route contracts
- changes to documented environment-variable contracts
- intentionally retiring a public application capability while preserving same-major upgradeability

When MINOR increments, PATCH resets to `0`.

### PATCH

Use PATCH for all other changes that do not meet MAJOR or MINOR criteria.

Typical PATCH examples include:

- admin-only changes
- scripts and tooling updates
- dependency updates
- documentation updates
- tests and examples
- app seed data changes
- CI/workflow rule updates
- internal settings/configuration updates that do not change a documented environment-variable contract

PATCH releases should advance normally during active development rather than leaving a major line artificially frozen at `.0.0` until a large feature appears.

## Database compatibility contract

Within a major line, supported upgrades use Django migrations normally. For example, databases in the `1.x` line are expected to move forward through later `1.x` releases with Django migrations.

Across a major boundary, direct Django migration is not a compatibility guarantee. The supported path is:

1. run the source major version against its own database
2. export/reconcile durable domain data through the versioned reconciliation contract
3. create a fresh database using the target major's migration lineage
4. import/reconcile the durable data into the target schema
5. verify reconciliation invariants before cutover

The old release tag/branch retains its own complete migration history for reproducibility. The new major does not need to preserve that history in its active migration graph.

See [Major-Version Migration and Reconciliation](major-version-migration-and-reconciliation.md).

## Arthexis 1 and 2

Arthexis 1 is the current cleanup and domain-separation line. During `1.x` we should aggressively remove runtime ownership that belongs in GWay, deprecate obsolete application surfaces, and keep only as much migration compatibility machinery as is required to upgrade supported `1.x` databases.

Arthexis 2 is the first planned clean schema-generation boundary under this policy. Before cutting `2.0.0`, the reconciliation engine must be capable of moving supported durable Arthexis 1 domain state into a fresh Arthexis 2 database and verifying the result.

Migration-only shells and other historical compatibility code that exist solely for the Arthexis 1 migration graph may be deleted when the Arthexis 2 migration lineage is created.

## Release procedure ownership

Developers should not manually edit `VERSION` while implementing ordinary changes.

The release procedure selects the next version by applying this policy to the full set of changes included in a release. Automatic release-impact detection establishes the minimum PATCH or MINOR level for ordinary changes. MAJOR is a deliberate release decision because it also declares a new schema generation.

The automated planner should therefore treat:

- app additions as at least MINOR
- app removals as at least MINOR
- public UI, route, API, serializer, consumer, environment, and model-contract changes as at least MINOR
- docs, tests, scripts, workflow changes, internal settings/configuration changes, and admin-only changes as PATCH unless a higher rule applies
- a requested MAJOR bump as an explicit schema-generation decision

Maintainers may force a higher bump level. Manual inputs are a floor: they may raise the computed bump level but must not downgrade automatic release-impact evidence.

Version advancement is collapsed to a single step per release:

- do not increment once per file or once per app change
- select the highest required bump level found in the release diff
- apply that bump once

## Summary

- MAJOR = deliberate new schema generation; cross-major data movement uses reconciliation rather than Django migration history.
- MINOR = meaningful application/domain contract change inside the same schema generation, including app addition or removal.
- PATCH = all other release-worthy changes.
- Django migrations are a same-major compatibility contract.
- Arthexis 1 is the cleanup line; Arthexis 2 is the first planned fresh migration lineage.
