# Retained 2.0 domain contracts

This is the affirmative contract gate for V200-02. An app or record is not
carried into 2.0 merely because it exists in the frozen 1.x checkout.

## Selected application surface

| Owner app | Domain contract | Admin/public boundary | Retained data | Explicit exclusions | Test boundary |
| --- | --- | --- | --- | --- | --- |
| `base` | Shared seed identity, natural-key and ownership primitives | Admin metadata only; no public routes | Minimal common fields required by selected domains | Release, dashboard, migration-helper state | Migration and seed marker |
| `celery` | Task discovery and OCPP-only scheduling policy | Operational task configuration; no public routes | No domain records | Host maintenance and non-OCPP schedules | Task registration policy |
| `events` | Structured JSON event publication and publish command | Admin diagnostics; no public routes | Event envelopes only if a retained producer requires persistence | Generic notification/mail history | Envelope and failure behavior |
| `nodes` | Identity, registration, four roles, topology and retained messages | Admin management; no public routes in the foundation | Node identities, relations and registration state | Features, host inspection, credentials, upgrades and network discovery | Role invariant and topology contracts |
| `sigils` | Root registry and Django extraction adapter | Admin root inspection; no public routes | Roots for retained content types | Removed-app roots and local parser/evaluator | Adapter and safe extraction |
| `cards` | Logical credentials, authorization and audit attempts | Admin credential policy; no reader endpoints | Labels, state, account/user links and approved watchlist state | Reader/writer hardware, raw transport state, keys and scanner services | Authorization lookup and audit policy |
| `energy` | Accounts, tariffs, balances/ledgers and charging attribution | Admin account and tariff management; no public routes in the foundation | Accounts, direct OCPP ID tags, credits, ledgers and attribution | Payment, Odoo, maps and unrelated reports | Ledger and attribution invariants |
| `ocpp` | CSMS transport, protocol behavior, charging domain and simulator boundary | Admin controls plus WebSocket/ASGI transport | Chargers, EVSE/connectors, transactions, meter values, profiles, reservations, vendor guides and approved PKI | Host networking, generic sites/media and removed-app data | Protocol handlers, persistence and simulator boundary |

All eight project apps use the same installed registry for every node role.
Role-specific behavior is a runtime policy inside a retained owner, not dynamic
app installation.

## Cross-domain decisions

* Cards link to the configured Django auth user and energy account through
  stable 2.0 identifiers; source primary keys are not reconciliation keys.
* OCPP owns protocol records, charger locations needed by charging behavior,
  and approved PKI records. It does not own NetworkManager or host TLS state.
* Events and Celery remain infrastructure boundaries for retained OCPP flows;
  they do not reintroduce generic mail, host, repository, or upgrade services.
* `Constellation` is accepted only as an input alias for the `Watchtower` role.
* No legacy import is part of this contract task. V200-04 will define the
  export and reconciliation artifact after destination models stabilize.

## Deliberately unselected surface

Feature packs, dynamic app discovery, host lifecycle/network management,
general-purpose sites/media/reports, external-office/payment integrations,
physical RFID services, and historical migration-only packages are not 2.0
application contracts. Their data and runtime behavior remain in the frozen
1.x source only for reference and later disposition review.

## Implemented runtime core

The first 2.0 runtime slice provides an OCPP 1.6 WebSocket route for known,
active chargers and supports `BootNotification`, `Heartbeat`, `Authorize`,
`StatusNotification`, `StartTransaction`, and `StopTransaction`. Authorization
uses the retained logical-card and direct-account policy, records an audit
attempt, and attempts non-fatal structured event publication.

Celery currently owns only the OCPP stale-connection maintenance task and its
hourly schedule. The `event publish` and `event pub` command contract persists
structured JSON envelopes. All retained OCPP 1.6 and 2.0.1 action contracts
have handlers or validators, with in-process protocol-client coverage. Broker-
backed lifecycle deployment and advanced scheduling remain separate work.
