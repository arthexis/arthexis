# Major-Version Migration and Reconciliation

This guide defines how Arthexis crosses a major-version boundary.

## Principle

Within a major release line, Django migrations are authoritative and supported. Across major release lines, durable business/domain data is authoritative and the Django migration graph is not.

A major upgrade therefore rebuilds the target installation from durable state instead of mutating the previous major's schema into the new one.

```text
Arthexis N database
        |
        | export / reconcile
        v
versioned reconciliation representation
        |
        | import / reconcile
        v
fresh Arthexis N+1 database
```

Directly running Arthexis `N+1` migrations against an Arthexis `N` database is unsupported unless a release explicitly documents an exception.

## Reconciliation contract

The reconciliation format must describe stable domain concepts rather than Django table names, app labels, migration names, or raw primary-key relationships.

The engine should provide versioned resources with three responsibilities:

- export durable source data
- import or transform it into the target schema
- verify domain invariants after import

Resources should be independently registered by the applications that own the corresponding domain data.

A conceptual interface is:

```python
class ReconciliationResource:
    name = "chargers"
    version = 1

    def export(self, source): ...
    def import_(self, target, records): ...
    def verify(self, source, target): ...
```

The serialized representation should include its own format version and source Arthexis version. It must not rely on Django's `dumpdata` format as the compatibility contract.

## Stable reconciliation identities

Durable entities that must survive major upgrades need stable logical identities that are independent of database row numbers. Integer primary keys may remain useful internally, but reconciliation should prefer UUIDs or domain-natural identifiers for durable references.

Relationships in the reconciliation representation should refer to those stable identities rather than source database primary keys.

## Resource lifecycle

Every persistent resource considered during a major upgrade should have an explicit disposition:

- **retain** — copy the same domain concept forward
- **transform** — map the source representation into a changed target representation
- **merge** — combine multiple source concepts into one target concept
- **split** — expand one source concept into several target concepts
- **discard** — intentionally retire data that is no longer part of the supported domain

Discarded data must be explicit and auditable rather than silently omitted.

## Reliability requirements

The reconciliation process should be deterministic, dependency-aware, restartable where practical, and auditable.

A run should record per-resource results such as exported, imported, transformed, skipped, failed, and verified counts. Import completion alone is insufficient; important resources must define verification invariants such as stable identifier sets, totals, uniqueness constraints, and relationship integrity.

## CI contract

For a current major line, CI should prove:

- a clean database can be created from that major's migrations
- supported earlier releases in the same major can migrate forward
- the migration graph remains internally consistent

For a new major line, CI should additionally prove:

- a fresh target-major database can be created from empty state
- representative source-major fixture databases reconcile into that fresh database
- reconciliation verification passes

CI should not establish a guarantee that a previous-major database can be upgraded by running the new major's Django migration graph directly.

Representative source databases should include difficult historical cases, including data owned by applications retired during the source major.

## Arthexis 1 cleanup policy

Arthexis 1 is intentionally a cleanup and separation line.

During `1.x`:

- remove obsolete runtime code aggressively
- move infrastructure capabilities to GWay where they belong
- deprecate or remove obsolete public application surfaces through normal MINOR releases
- preserve only the migration compatibility shells required to keep supported `1.x` upgrades working
- avoid spending disproportionate effort beautifying historical migration graphs that will not be inherited by Arthexis 2

A retired app may therefore remain as a migration-only shell during `1.x` even after its runtime implementation disappears.

## Arthexis 2 readiness gate

Arthexis `2.0.0` is the first planned major release to use a fresh Django migration lineage under this policy.

Before `2.0.0` is cut, all of the following must be true:

1. A reconciliation engine and resource registry exist.
2. A schema-generation marker can distinguish Arthexis 1 databases from Arthexis 2 databases.
3. Startup/update tooling prevents accidentally applying Arthexis 2 migrations to an Arthexis 1 database.
4. Durable business/domain entities have stable reconciliation identities where needed.
5. Core resource export, import, and verification are implemented.
6. Representative Arthexis 1 databases reconcile successfully into a fresh Arthexis 2 database in CI.
7. Every retired or reorganized persistent resource has an explicit retain/transform/merge/split/discard decision.
8. Fresh Arthexis 2 initial migrations describe the desired schema without depending on the Arthexis 1 migration lineage.
9. The cutover procedure includes backup, reconciliation, verification, and rollback instructions.

## Application retirement and major boundaries

When an app is retired during a major line, focus first on removing runtime ownership and callers. Keep a migration-only compatibility shell only when the current major's migration graph still needs that app identity.

At the next major boundary, migration-only shells that exist solely for the previous generation may be removed completely. Their old migrations remain available from the previous release branch/tag for reproducibility, but they do not need to survive in the new active migration lineage.
