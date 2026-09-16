# Versioning and Maturity Policy

This policy defines how Arthexis versions are advanced during the release procedure.

Arthexis versions follow `MAJOR.MINOR.PATCH`.

The version number describes both application maturity and database compatibility. The central rule is:

> Django migration compatibility is guaranteed within a major release line. A new major release starts a new schema generation and is reached through reconciliation, not by replaying the new major's Django migration graph against the previous major's database.

A second invariant governs ordinary version advancement:

> A new PATCH version is earned by a successful deployment of Arthexis, not merely by merging source code.

This makes the version sequence a deployment ledger. A source commit may exist on `main` without receiving a new patch number. Once that exact `main` revision is successfully deployed and passes the Arthexis live-integration checks, automation may create the version-attestation PR that advances PATCH.

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

A deliberate MINOR bump may be included in the source revision that is deployed. If the successfully deployed revision already changed `VERSION`, that explicit version change is itself the version attestation and automation must not immediately add another PATCH bump.

### PATCH

PATCH is the normal deployment-progress counter inside the current MAJOR.MINOR line.

When an exact Arthexis `main` revision is successfully deployed through the designated Arthexis live-integration/deployment environment and that revision did not already change `VERSION`, automation advances PATCH by one through a protected version-attestation pull request.

Examples:

- `1.0.7` source changes merge to `main`; no version change happens yet.
- that exact `main` revision is deployed successfully; automation opens the attestation PR for `1.0.8`.
- merging the version-only attestation PR does not count as a new deployable application change and must not recursively create `1.0.9`.
- if a deliberate `1.1.0` change is deployed successfully, that explicit MINOR version already represents the deployment and no automatic `1.1.1` is created for the same deployment.

PATCH numbers therefore mean more than commit count: they identify successfully exercised deployed states.

## Deployment-attested versioning

The designated Arthexis deployment workflow is the release clock for ordinary PATCH movement.

The workflow must:

1. resolve one exact `main` commit SHA before deployment
2. deploy and exercise that exact revision
3. verify application health and the live-integration contract
4. verify that `main` still points to the tested SHA before assigning a version
5. refuse to assign a version to a newer, untested `main` revision if `main` moved during the run
6. if the tested revision already changed `VERSION`, treat that explicit MAJOR/MINOR/PATCH change as the attestation for the deployment
7. otherwise create a version-only PR that increments PATCH by exactly one
8. never treat the version-only attestation commit itself as a new deployment candidate

The attestation PR must go through normal protected-branch checks. Arthexis does not bypass the `main` ruleset merely to write version metadata.

Only one unmerged deployment-version attestation should exist at a time. The version sequence is intentionally linear; another deployment should not mint a competing next patch while the previous attestation remains unresolved.

The deployment workflow is expected to live with Arthexis rather than being permanently coupled to whichever GWay repository happens to host the current Watchtower bootstrap. GWay provides the deployment/runtime capability; Arthexis owns the decision that an Arthexis revision has passed its deployment gate and earned a version.

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

Developers should not manually edit `VERSION` for ordinary patch-level work.

Automatic release-impact detection still classifies the semantic minimum level of source changes:

- app additions are at least MINOR
- app removals are at least MINOR
- public UI, route, API, serializer, consumer, environment, and model-contract changes are at least MINOR
- docs, tests, scripts, workflow changes, internal settings/configuration changes, and admin-only changes are PATCH-level changes unless a higher rule applies
- a requested MAJOR bump is an explicit schema-generation decision

MAJOR and MINOR transitions remain deliberate release decisions. Ordinary PATCH advancement is deployment-attested and automated after the live deployment succeeds.

Maintainers may force a higher bump level. Manual inputs are a floor: they may raise the computed bump level but must not downgrade automatic release-impact evidence.

## Summary

- MAJOR = deliberate new schema generation; cross-major data movement uses reconciliation rather than Django migration history.
- MINOR = meaningful application/domain contract change inside the same schema generation, including app addition or removal.
- PATCH = successful deployment progress within the current MAJOR.MINOR line.
- PATCH does not advance merely because a commit lands on `main`.
- A successful deployment of an unchanged VERSION opens a protected version-attestation PR for the next patch.
- Django migrations are a same-major compatibility contract.
- Arthexis 1 is the cleanup line; Arthexis 2 is the first planned fresh migration lineage.
