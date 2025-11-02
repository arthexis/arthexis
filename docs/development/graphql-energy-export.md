# GraphQL energy export proposal

## Overview

Arthexis records detailed meter readings and transaction summaries for each charging
session, but the only export path today is the admin JSON export that reuses
`ocpp.transactions_io.export_transactions`. That flow is designed for bulk
synchronization and is not well suited for near-real-time metering dashboards or
research workloads that need to slice data by charger, tariff zone, or time
window. A GraphQL endpoint would provide a typed, discoverable API that lets
clients retrieve exactly the energy data they need without writing custom SQL or
polling the administrative export.

## Goals

* Publish energy usage information gathered from `Transaction` and `MeterValue`
  records through a GraphQL endpoint while preserving the field semantics that
  the dashboard already relies on.
* Support filtering by time range, charger, location metadata, and account so
  that both utility metering integrations and academic researchers can focus on
  the subset that matters to them.
* Offer both aggregated totals (kWh per charger, location, or account) and
  detailed meter reading time series, so the same API can feed monthly billing
  reports as well as load-shape analyses.
* Reuse existing authentication (Django sessions or API tokens) and the
  role-based permissions that already gate access to the charging data.

## Non-goals

* Replacing or removing the existing admin export flow.
* Introducing subscription/real-time push updates in the first iteration.
* Redesigning the energy accounting logic that `Transaction.kw` currently uses.

## Data sources and existing behavior

* `ocpp.models.Transaction.kw` derives per-session energy in kWh by combining
  start/stop meter values and any interpolated readings while a session is
  active. This is the core value that billing reports already display.
* `ocpp.models.MeterValue` stores the normalized per-interval meter readings,
  including the `energy` decimal column that is populated through the
  `MeterReadingManager` normalization logic.
* `ocpp.transactions_io.export_transactions` illustrates which related models
  and fields are needed to reconstruct a session export. The GraphQL resolvers
  can reuse the same queryset composition (selecting chargers and prefetching
  meter values) to avoid N+1 queries.

## Proposed GraphQL stack

1. Add `graphene-django` (or Strawberry-Django if the team prefers async schemas)
   to `pyproject.toml` and `requirements.txt`. Graphene already integrates well
   with Django ORM querysets and supports Relay pagination out of the box.
2. Create a lightweight `api` Django app that holds schema definitions,
   dataloaders, and tests. Wiring the schema through `urls.py` keeps the GraphQL
   concerns separate from the OCPP app while still living inside the main
   project.
3. Expose the endpoint at `/graphql/` using `GraphQLView` with CSRF protection
   disabled only for token-authenticated requests. Staff users can continue to
   rely on session auth when using the GraphiQL explorer in development.

## Schema design

```graphql
schema {
  query: Query
}

type Query {
  energySessions(filter: EnergySessionFilter!, pagination: PaginationInput): EnergySessionConnection!
  energyTotals(filter: EnergyAggregateFilter!): [EnergyTotal!]!
}

type EnergySession {
  id: ID!
  chargerId: String!
  connectorId: Int
  account: String
  startedAt: DateTime!
  stoppedAt: DateTime
  energyKwh: Float!
  meterValues: [MeterReading!]!
}

type MeterReading {
  timestamp: DateTime!
  context: String
  connectorId: Int
  energyKwh: Float
  voltage: Float
  currentImport: Float
  temperature: Float
  soc: Float
}

type EnergyTotal {
  group: String!
  intervalStart: DateTime!
  intervalEnd: DateTime!
  energyKwh: Float!
}
```

* `EnergySessionFilter` accepts charger IDs, account IDs, location IDs (via
  `Charger.location`), and a time range. Time slicing uses `start_time` and
  `stop_time` to ensure consistency with existing dashboards.
* `EnergyAggregateFilter` adds a `groupBy` enum (e.g., `CHARGER`, `LOCATION`,
  `ACCOUNT`, `DAY`) so clients can request aggregated totals in a single query.
* `meterValues` is populated from the prefetched queryset to avoid extra
  database trips. Converting the stored decimal to float mirrors the JSON export
  behavior and keeps the schema simple for researchers.
* Use Relay-style connections for `energySessions` so large historical exports
  can be paginated by cursor. The `PaginationInput` supports forward pagination
  and an optional ordering toggle (`OLDEST_FIRST` vs `NEWEST_FIRST`).

## Resolvers and performance

* Base queryset lives in a service helper that encapsulates the annotations used
  in the admin export, with optional `Prefetch` for meter values.
* Apply Django filters for time range and chargers. For aggregates, use ORM
  annotations (`Sum`, `TruncDay`, etc.) so the database handles grouping.
* Use `graphene-django-optimizer` (if adopted) or manual select/prefetch to keep
  query counts low, especially when fetching nested meter values.
* For deployments with large meter datasets, add optional caching of aggregate
  queries (e.g., Redis keyed by filter tuple) to avoid recalculating totals for
  repeated dashboard views.

## Authorization and auditing

* Reuse the same permission checks that guard the admin export button—only staff
  or users with an `export_transactions` capability can access the GraphQL
  endpoint. Wrap resolvers in a decorator that raises a GraphQL error when the
  requesting user lacks `ocpp.view_transaction` and `ocpp.view_metervalue` perms.
* Log each GraphQL request (filters and result counts) via the existing audit
  logger so that exporting sensitive energy data leaves a trail comparable to the
  admin export download.
* For long-running bulk pulls, encourage the use of API tokens tied to service
  accounts. Tokens can be throttled separately to protect the primary database.

## Implementation roadmap

1. **Foundation**
   * Add dependency, create schema module, wire `/graphql/` URL, and implement a
     smoke-test resolver that returns a static value.
   * Add unit tests covering authentication guardrails and the baseline query
     structure.
2. **Energy session query**
   * Implement queryset helpers that mirror `export_transactions` prefetching.
   * Build the `EnergySession` type and resolver, including pagination and meter
     values.
   * Add tests that assert energy totals line up with `Transaction.kw` for a set
     of fixtures covering active, completed, and incomplete sessions.
3. **Aggregation query**
   * Add `EnergyTotal` type, filter enum, and resolver using ORM aggregation.
   * Cover grouping behaviors and edge cases (no data, invalid filters) with
     tests.
4. **Operational hardening**
   * Document token usage, rate limiting, and GraphiQL access in `docs/`.
   * Add metrics (e.g., Prometheus counter) around GraphQL usage if observability
     is already in place.

## Testing strategy

* Unit tests for resolvers to verify filters and permission checks.
* Snapshot tests for representative GraphQL queries to lock down the schema.
* Performance regression test (optional) that loads a large fixture set and
  asserts the resolver executes within an acceptable time bound.

## Future enhancements

* Introduce subscriptions once WebSocket infrastructure is available so
  real-time dashboards can receive energy updates without polling.
* Layer in derived metrics (e.g., peak demand, average session duration) through
  additional GraphQL fields as research requirements evolve.
* Expand filters to include tariff zone once the tariff metadata is populated in
  `Charger` records.
