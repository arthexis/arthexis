# Versioning and Maturity Policy

This policy defines how Arthexis versions are advanced during the release procedure.

Arthexis versions follow `MAJOR.MINOR.PATCH`.

The version number describes both application maturity and database compatibility. The central rule is:

> Django migration compatibility is guaranteed within a major release line. A new major release starts a new schema generation and is reached through reconciliation, not by replaying the new major's Django migration graph against the previous major's database.

A second invariant governs ordinary version advancement:

> `VERSION` on `main` is the version currently being developed and tested. A successful deployment attests that candidate version to the exact deployed SHA; only then does `main` roll forward to the next patch candidate.

This separates the deployed version from the next development version without needing opaque commit identifiers as the primary progress signal. The Watchtower may remain on the exact SHA and VERSION that passed deployment while `main` advances its VERSION-only metadata to the next candidate.

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

A deliberate MINOR or MAJOR transition sets the next candidate version before that candidate is deployed. The version is not considered deployment-attested until the corresponding SHA passes the deployment gate.

### PATCH

PATCH is the normal deployment-progress counter inside the current MAJOR.MINOR line.

If `main` carries candidate `1.0.7`, a successful deployment of that exact SHA proves `1.0.7`. Automation then opens a protected VERSION-only pull request that rolls `main` forward to candidate `1.0.8`.

The rollover commit is metadata for future work. It is not itself a new deployable application change, does not make the Watchtower become `1.0.8`, and must not recursively earn another patch.

Examples:

- `main` carries `1.0.7` while ordinary source changes accumulate.
- an exact `main` SHA is deployed successfully with `VERSION=1.0.7`; that SHA is now the attested `1.0.7` state.
- automation opens `[version-rollover] 1.0.8`.
- merging that VERSION-only PR means new work is now being developed as candidate `1.0.8`; the Watchtower may still correctly run the proven `1.0.7` SHA.
- the next substantive deployment that carries `1.0.8` can attest `1.0.8`, after which `main` rolls to `1.0.9`.

PATCH therefore measures proven deployment progress rather than source-commit count.

## Deployment-attested versioning

The designated Arthexis Watchtower workflow is the release clock for ordinary PATCH movement.

The workflow must:

1. resolve one exact candidate SHA and VERSION before deployment
2. deploy and exercise that exact revision
3. verify application health and the live-integration contract
4. verify that the source branch still points at the tested SHA before attesting it
5. bind the candidate VERSION to that successful SHA
6. create a VERSION-only PR that rolls the repository forward to the next patch candidate
7. never treat the VERSION-only rollover commit itself as a new deployment candidate

The rollover PR must go through normal protected-branch checks. Arthexis does not bypass the `main` ruleset merely to write version metadata.

Only one unmerged rollover should exist for a repository at a time. The version sequence is intentionally linear.

## Arthexis as Watchtower orchestrator

The deployment workflow should live with Arthexis rather than being permanently coupled to GWay Wire or another infrastructure repository.

The dependency direction is intentional:

- Arthexis may depend on GWay and GWay peer packages for deployment/runtime capability.
- GWay packages must not depend on Arthexis merely to participate in deployment.
- a change in any managed GWay peer may be exercised by the Arthexis Watchtower even when Arthexis itself did not change.

Arthexis therefore acts as the deployment orchestrator, while each repository retains ownership of its own VERSION history.

## Peer deployment attestation

The Watchtower should maintain a deployment manifest for every managed repository. A manifest entry records at least:

- repository identity
- candidate VERSION
- exact deployed commit SHA

At the beginning of a run, the Watchtower snapshots the candidate manifest. After a successful deployment, it compares that snapshot with the previous successfully attested manifest.

A repository earns its current candidate version only when its substantive deployed SHA changed and the complete Watchtower deployment succeeded. Repositories that did not change do not roll their VERSION merely because another peer changed.

A pure VERSION-rollover commit is metadata and must not count as a substantive SHA change for deployment-attestation purposes.

For example, if a deployment contains new Arthexis and GWay Wire code but unchanged GWay Web code:

- Arthexis earns its current candidate version and receives a rollover PR.
- GWay Wire earns its current candidate version and receives a rollover PR.
- GWay Web receives no version change.

This peer-aware mechanism can later be standardized across the GWay ecosystem without making those repositories depend on Arthexis at runtime.

Cross-repository rollover creation requires a credential or GitHub App with narrowly scoped contents/pull-request write access to the managed peer repositories. The ordinary repository-scoped `GITHUB_TOKEN` is insufficient for writing version PRs into sibling repositories.

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

Developers should not manually increment PATCH for ordinary work. `VERSION` on `main` already names the candidate being developed.

Automatic release-impact detection still classifies the semantic minimum level of source changes:

- app additions are at least MINOR
- app removals are at least MINOR
- public UI, route, API, serializer, consumer, environment, and model-contract changes are at least MINOR
- docs, tests, scripts, workflow changes, internal settings/configuration changes, and admin-only changes are PATCH-level changes unless a higher rule applies
- a requested MAJOR bump is an explicit schema-generation decision

MAJOR and MINOR transitions remain deliberate release decisions. Ordinary PATCH rollover happens only after the current candidate successfully deploys.

Maintainers may force a higher bump level. Manual inputs are a floor: they may raise the computed bump level but must not downgrade automatic release-impact evidence.

## Summary

- MAJOR = deliberate new schema generation; cross-major data movement uses reconciliation rather than Django migration history.
- MINOR = meaningful application/domain contract change inside the same schema generation, including app addition or removal.
- `VERSION` on `main` = the version currently being developed/tested.
- successful deployment binds that candidate VERSION to the exact tested SHA.
- after success, a VERSION-only PR rolls `main` to the next patch candidate.
- version-only rollover commits do not trigger deployment or earn versions.
- Arthexis is the preferred Watchtower orchestrator for itself and its GWay peers.
- a peer rolls forward only when its substantive deployed SHA changed in a successful Watchtower run.
- Django migrations are a same-major compatibility contract.
- Arthexis 1 is the cleanup line; Arthexis 2 is the first planned fresh migration lineage.
