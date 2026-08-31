# OCPP Application Study Guide

This guide is for staff and developers who need to understand the Arthexis
OCPP app without reading the whole codebase at once. It explains what the app
owns, where to start, and how the main charger workflows move through the
system.

For protocol-by-protocol behavior, read the
[OCPP 1.6 user manual](ocpp-user-manual.md) after this guide.

## The Short Version

The OCPP app is the charger-facing control and reporting layer for Arthexis. It
acts as the central system that chargers connect to, records charger state and
transactions, exposes operator controls, and supports explicit signed
import/export with trusted nodes.

Keep this mental model:

1. A charger connects to an OCPP WebSocket route.
2. The consumer identifies the charger and records live state in memory.
3. Incoming charger messages update database models such as `Charger`,
   `Transaction`, and `MeterValue`.
4. RFID and account checks decide whether a tag may start a session.
5. Operator actions send explicit CSMS-to-charger calls through the active
   WebSocket session.
6. Reports and exports read the stored transaction data.
7. Node-to-node operations use explicit signed request/response endpoints, not
   continuous OCPP/WebSocket forwarding.

## What To Study First

Start with these four files in order:

| Step | File | What it teaches |
| --- | --- | --- |
| 1 | `apps/ocpp/routing.py` | Which WebSocket paths reach the charger consumer. |
| 2 | `apps/ocpp/consumers/csms/consumer.py` | The main CSMS consumer and the mixins that implement charger behavior. |
| 3 | `apps/ocpp/models/charger.py` | The persistent charger identity, status, authorization, and display state. |
| 4 | `apps/ocpp/models/transaction.py` | How a charging session is stored and how energy is calculated. |

Then use the rest of this guide to follow one workflow at a time.

## Vocabulary

| Term | Meaning in Arthexis |
| --- | --- |
| Charger | A `Charger` row, usually identified by the serial or charger id reported over OCPP. |
| Connector | A physical connector on a charger. Some rows are aggregate charger rows and some are connector-specific rows. |
| CSMS | The central system side. In Arthexis this is the Django Channels consumer. |
| RFID | A card or tag managed by `apps.cards` and used to authorize charging sessions. |
| Transaction | A charging session stored in `Transaction`, with optional `MeterValue` samples. |
| Meter value | A parsed energy, voltage, current, temperature, or state-of-charge sample. |
| Pending call | In-memory metadata for an operator-initiated command waiting for a charger response. |
| Node import/export | Explicit signed payload exchange between trusted nodes for chargers and transactions. |

## Runtime Boundaries

The app has three main boundaries:

- **WebSocket boundary**: charger devices connect through `apps/ocpp/routing.py`
  into `CSMSConsumer`.
- **HTTP/admin boundary**: operators use pages, admin actions, and public
  charger views from `apps/ocpp/urls.py`, `apps/ocpp/views/`, and
  `apps/nodes/views/ocpp.py`.
- **Node exchange boundary**: trusted nodes call signed endpoints in
  `apps/nodes/views/network.py`, which delegate charger and transaction payload
  work to `apps/ocpp/network.py`.

Do not treat legacy relay state as the supported runtime architecture. New work
should use explicit operations such as charger, RFID, authorized-card, and
transaction import/export.

## Flow 1: Charger Connection

Read this path when a charger appears offline, connects to the wrong charger
row, or negotiates the wrong protocol version.

1. `apps/ocpp/routing.py` maps charger WebSocket paths to `CSMSConsumer`.
2. `CSMSConsumer` combines connection, identity, transport, RFID, transaction,
   metering, notification, and dispatch mixins.
3. The connection and identity mixins extract the charger id from the path or
   query string, normalize it, and attach the session to the right `Charger`
   row.
4. `apps/ocpp/store/` keeps live connection state, active transactions,
   pending calls, session logs, and timers.
5. The database remains the durable record. The store is the live coordination
   layer used while sockets are connected.

Useful files:

- `apps/ocpp/consumers/base/connection.py`
- `apps/ocpp/consumers/base/connection_flow.py`
- `apps/ocpp/consumers/base/identity.py`
- `apps/ocpp/consumers/csms/transport.py`
- `apps/ocpp/store/state.py`

## Flow 2: Status And Heartbeats

Read this path when the admin badge, public status page, or last-seen timestamp
does not match what the charger is reporting.

1. The charger sends `BootNotification`, `Heartbeat`, or `StatusNotification`.
2. The CSMS consumer dispatches the OCPP action to a focused handler.
3. Status handlers update the aggregate charger row and connector-specific row
   when appropriate.
4. The app stores reported status, error code, vendor info, availability state,
   and timestamps on `Charger`.
5. Display helpers turn raw state into badges and public-facing status text.

Useful files:

- `apps/ocpp/consumers/csms/actions.py`
- `apps/ocpp/consumers/csms/handlers/status.py`
- `apps/ocpp/models/charger.py`
- `apps/ocpp/status_display.py`
- `docs/ocpp_cp_statuses.md`

## Flow 3: RFID Authorization

Read this path when a card is accepted unexpectedly, rejected unexpectedly, or
needs to be shared across nodes.

1. A charger sends `Authorize`, `StartTransaction`, or an OCPP 2.x transaction
   event with an id tag.
2. `RfidMixin` looks up matching `RFID` rows from `apps.cards`.
3. The charger authorization policy decides the result:
   - `strict` expects an authorized linked account.
   - `allowlist` permits known allowed unlinked tags.
   - `open` accepts any tag and is explicitly insecure compatibility behavior.
4. Each decision carries a reason code for auditability.
5. RFID attempts are stored so operators can inspect accepted and rejected
   scans later.

Useful files:

- `apps/ocpp/consumers/base/rfid.py`
- `apps/cards/models.py`
- `apps/cards/sync.py`
- `apps/cards/admin.py`
- `apps/energy/models.py`

## Flow 4: Transactions And Energy

Read this path when a charging session, meter value, or energy report looks
wrong.

1. `StartTransaction` creates a `Transaction` when authorization succeeds.
2. `MeterValues` writes parsed samples to `MeterValue`.
3. `StopTransaction` finalizes the transaction and stores stop readings and
   timestamps.
4. `Transaction.kw` calculates consumed energy in kWh from start/stop meter
   values or from the first and last energy samples.
5. Reports and export tools read stored transaction data; they do not require a
   live charger socket.

Useful files:

- `apps/ocpp/consumers/base/actions_transactions.py`
- `apps/ocpp/consumers/base/transactions.py`
- `apps/ocpp/consumers/base/metering.py`
- `apps/ocpp/models/transaction.py`
- `apps/ocpp/models/meter_value.py`
- `apps/ocpp/transactions_io.py`
- `apps/ocpp/views/reports.py`

Helpful command:

```bash
.venv/bin/python manage.py ocpp transactions export /tmp/charger-transactions.json --start 2026-01-01 --end 2026-07-01 --all-chargers
```

## Flow 5: Operator Actions

Read this path when an admin or public control action does not reach the
charger, times out, or stores the wrong response.

1. The operator chooses an action such as remote stop, reset, diagnostics,
   trigger message, data transfer, get configuration, or change availability.
2. The view validates the request and finds the active charger session.
3. The app sends a CSMS-to-charge-point OCPP call over the live WebSocket.
4. The store records pending-call metadata and timeout handling.
5. The call-result or call-error handler reconciles the charger response back
   into model state and logs.

Useful files:

- `apps/ocpp/views/actions/`
- `apps/nodes/views/ocpp.py`
- `apps/ocpp/call_result_handlers/`
- `apps/ocpp/call_error_handlers/`
- `apps/ocpp/store/pending_calls.py`
- `apps/ocpp/store/scheduler.py`

## Flow 6: Explicit Node Import And Export

Read this path when a node needs charger or transaction data from another
trusted node.

1. A trusted node signs a request to the node network endpoint.
2. `apps/nodes/views/network.py` verifies the signature using the requester's
   registered node key.
3. Charger metadata is serialized by `serialize_charger_for_network`.
4. Transaction payloads are exported with `apps/ocpp/transactions_io.py`.
5. The receiving node applies charger payloads and imports transaction data
   with deduplication-oriented helpers.

Useful files:

- `apps/nodes/views/network.py`
- `apps/nodes/services/crypto.py`
- `apps/ocpp/network.py`
- `apps/ocpp/transactions_io.py`

Design rule: use explicit import/export for cross-node data movement. Do not
add always-on forwarding loops to solve study-guide, reporting, or RFID
synchronization problems.

## Study Exercises

Use these exercises to build confidence before changing code:

1. Trace one `StatusNotification` from WebSocket action name to the final
   `Charger` fields it updates.
2. Trace one RFID card decision and write down the policy, account lookup, and
   stored audit row.
3. Export transactions for a known date range and identify which fields come
   from `Transaction` versus `MeterValue`.
4. Find the view that sends `ChangeAvailability` and the handler that processes
   its result.
5. Read `apps/nodes/views/network.py` and explain why a signature is required
   before charger data can be imported.

## Safe Validation Commands

Use focused commands while studying or making small changes:

```bash
.venv/bin/python manage.py check
.venv/bin/python manage.py migrations check
.venv/bin/python manage.py test run -- apps/ocpp
.venv/bin/python manage.py test run -- apps/cards apps/nodes apps/ocpp
.venv/bin/python manage.py ocpp coverage --version 1.6
.venv/bin/python manage.py ocpp transactions export /tmp/transactions.json --all-chargers
```

For a documentation-only change, `git diff --check` plus
`.venv/bin/python manage.py check` is normally enough.

Preview documentation locally with:

```bash
.venv/bin/mkdocs serve
```

## Common Mistakes

- Do not confuse the live `apps/ocpp/store/` state with durable database state.
  The store coordinates connected sessions; models are the durable source.
- Do not assume every charger row is a physical charger. Connector-specific
  rows can exist alongside aggregate rows.
- Do not treat `open` authorization policy as production-safe. It is an
  insecure compatibility mode.
- Do not extend old forwarding concepts for new node workflows. Prefer signed
  explicit import/export.
- Do not debug energy totals from `Transaction.meter_start` alone. Meter values
  can supply the energy bounds when start/stop readings are missing.

## Where To Go Next

- Read [OCPP 1.6 user manual](ocpp-user-manual.md) for message behavior.
- Read [CP Statuses and Transitions](../ocpp_cp_statuses.md) for display rules.
- Read [RFID Card Layout](rfid-card-layout.md) for card data conventions.
- Read [Endpoint Inventory](../integrations/endpoint-inventory.md) for public
  and administrative URL surfaces.
