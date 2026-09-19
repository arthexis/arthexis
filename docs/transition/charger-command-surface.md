# Charger model and fleet-report surface plan

## Decision

OCPPX-06 keeps one app-wide Django management command, `charger`, for the
read-only fleet report. Explicit charger control belongs on the `Charger` model
and is exposed through GWAY's Django ingestion; it is not duplicated as a
Django management-command API.

```text
manage.py charger
manage.py charger --charger depot-a
gway get charger --identity depot-a - reset
gway get charger --identity depot-a - reset --hard
gway get charger --identity depot-a - start --id-token member-42 --connector 1
gway get charger --identity depot-a - stop
```

Invoking `charger` presents configured chargers at one captured moment: identity,
configured protocol, connection state, latest
connector state, active transaction count, meter-derived energy total, and last
contact. An unavailable or unresolved energy total is displayed as such; it is
never silently rendered as zero.

The initial model operations are `Charger.reset`, `Charger.start`, and
`Charger.stop`; there is no generic `send` command. They never accept a
protocol version, action name, payload, arbitrary JSON, or a generic key/value
escape hatch. GWAY obtains the selected model through its regular Django manager
surface and pipes it into the class-level operation. The protocol comes from
`charger.station_model.preferred_protocol`; delivery is rejected when it is
absent, unsupported, or differs from the negotiated live connection.

The management command's default inventory is read-only. A GWAY operation acts
on exactly one piped charger. `reset` requests a graceful reset by default and
accepts `--hard` for an immediate reset. `start` requires `--id-token` and
optionally accepts `--connector` for OCPP 1.6 or `--evse` for OCPP 2.0.1.
`stop` selects the sole active local transaction, or requires `--transaction`
when several exist. Raw tokens and sensitive response data are not printed or
stored in command logs.

The retained 1.x `chargers` command is deliberately not copied. Its legacy
flags, automatic-start flow, implicit default target, raw settings operations,
and many unrelated verbs conflict with the 2.0 explicit-operation boundary.
The 2.0 fleet command keeps the human-readable default table and repeatable
selectors; GWAY supplies verb-first model operations without a parallel CLI.

## Required transport boundary

The current `active_connections` registry lives only in the ASGI process. GWAY
executes the model operation outside that process, so it cannot safely call the
registry directly. Before a model operation is allowed to emit a frame,
OCPPX-06 must provide an explicit shared channel-layer bridge to the owning
WebSocket consumer. Development tests may use an in-memory layer in one
process; a multi-process deployment must set `ARTHEXIS_CHANNEL_REDIS_URL` for
the supported shared Redis channel layer. If it is unavailable, the model
operation fails before creating an operation record.
This is immediate delivery for a user-requested operation, not scheduling,
retrying, or autonomous charger control.

## Tasks

### Task CCLI-01: Build a truthful charger snapshot and delivery boundary

* Intent: Provide the data and cross-process transport needed for an app-wide
  report and explicitly delivered model operation.
* Scope: `apps/ocpp/models/assets.py`, `models/sessions.py`,
  `domain/sessions.py`, `transport/consumer.py`, `transport/operations.py`,
  `arthexis/settings.py`, one new OCPP migration, and focused model/transport
  tests.
* Constraints: Infer protocol only from `StationModel.preferred_protocol`; do
  not add a CLI version override. Persist connection presence on connect and
  clear it on disconnect. Record units and multipliers needed to calculate
  meter-derived kWh accurately; show an unresolved value instead of guessing.
  Use a shared channel-layer request only for an explicit model operation; do not add a
  polling loop, Celery sender, retry worker, or scheduled charger operation.
* Acceptance Criteria: A snapshot returns configured protocol, current
  connection/connector/session state, last contact, and non-fabricated energy
  totals. A model-created operation reaches its matching live consumer only
  through a configured shared channel layer; mismatched or unavailable delivery
  fails without a pending operation record.
* Verification Commands: `.venv/bin/python manage.py makemigrations --check
  --dry-run`; `.venv/bin/python manage.py migrate`; `.venv/bin/python manage.py
  test tests.ocpp.test_models tests.ocpp.test_transport`; `python -m ruff check
  --config pyproject.toml apps arthexis tests`.
* Out of Scope: Billing/ledger mutation, a feature-flag framework copied from
  1.x, scheduled delivery, and legacy database import.
* Depends on: OCPPX-05.
* Blocks: CCLI-02, CCLI-03, OCPPX-07.
* Parallel-safe: no.
* Risk/Rollback: high; primary failure is a model operation that claims an operation
  was delivered when it was not. Roll back the bridge and leave read-only
  snapshots available until shared delivery is configured.

**Implementation record (2026-09-17):** Complete. `ChargerConnection` now
tracks the authenticated consumer channel and negotiated protocol, while the
consumer records connection/disconnection presence. Meter values retain unit
and multiplier metadata, completed sessions retain only resolvable
`energy_kwh`, and snapshots represent unresolved energy as unknown. Explicit
delivery uses the persisted connection channel only after protocol validation;
it fails without creating an operation under the local in-memory layer and is
enabled for multi-process deployments by `ARTHEXIS_CHANNEL_REDIS_URL`.

### Task CCLI-02: Add the canonical inventory command

* Intent: Establish one discoverable, small command grammar for charger fleet
  inspection.
* Scope: `apps/ocpp/management/commands/charger.py`,
  `apps/ocpp/management/charger/` query/selection/rendering modules, command
  tests, and operator documentation.
* Constraints: `charger` with no arguments is the all-charger read-only table.
  `--charger` is repeatable; `--all` is explicit and mutually exclusive with
  named selectors. Keep every new Python module below 400 lines. Do not add
  `command.sh` support in this task because the fresh v2 repository has no
  command-wrapper contract yet.
* Acceptance Criteria: `charger` renders the fleet or selected snapshots.
  Empty selections, duplicate targets, unknown identities, and incompatible
  selector combinations fail clearly. Output has a captured-at time and does
  not expose connection tokens or sensitive payloads.
* Verification Commands: `.venv/bin/python manage.py test tests.ocpp.test_charger_command`; `.venv/bin/python manage.py charger`; `python -m ruff format --check --config pyproject.toml apps arthexis tests`.
* Out of Scope: Outbound OCPP operations, broad 1.x command compatibility,
  aliases, and a host-local shortcut or wrapper.
* Depends on: CCLI-01.
* Blocks: CCLI-03.
* Parallel-safe: no.
* Risk/Rollback: low; primary failure is ambiguous fleet selection. Roll back
  the selection helper while retaining the canonical `charger` command.

**Implementation record (2026-09-17):** Complete. The canonical app-wide,
read-only
`charger` command renders one captured table from the snapshot service. Named
selections reject unknown identities and duplicate or mixed `--all` targets;
default invocation remains the whole-fleet inventory.

### Task CCLI-03: Add typed reset, start, and stop model operations

* Intent: Provide the initial local-state-aware charger controls without
  exposing arbitrary protocol payload injection.
* Scope: `apps/ocpp/models/assets.py`, `gway.toml`, model-operation tests, and
  this operator reference.
* Constraints: Implement only `reset`, `start`, and `stop`. Infer version from
  the selected charger's configuration and use the shared delivery boundary.
  `reset` defaults to soft/OnIdle and `--hard` selects Hard/Immediate. `start`
  requires `--id-token`; `stop` derives its target from the active local
  transaction unless `--transaction` disambiguates it. Do not add aliases,
  generic JSON, arbitrary action names, retries, or background sending.
* Acceptance Criteria: OCPP 1.6 emits Reset, RemoteStartTransaction, and
  RemoteStopTransaction with their typed payloads. OCPP 2.0.1 emits Reset,
  RequestStartTransaction, and RequestStopTransaction with their typed
  payloads. Every mutation has one explicitly piped target and reports a
  distinct pending operation; invalid local state or version-specific options fail
  before delivery.
* Verification Commands: `.venv/bin/python manage.py test tests.ocpp.test_charger_model_operations`; `gway help reset charger`; `python -m ruff check --config pyproject.toml apps arthexis tests`.
* Out of Scope: Every other outbound OCPP action, remote reads, admin bulk
  actions, operational orchestration, raw payload console input, aliases, and
  configuration migration from 1.x.
* Depends on: CCLI-01, CCLI-02.
* Blocks: CCLI-04, OCPPX-07.
* Parallel-safe: no.
* Risk/Rollback: medium; primary failure is composing the wrong versioned
  payload. Roll back the isolated operation helper and keep that action
  unavailable until its typed contract passes.

**Implementation record (2026-09-19):** Complete. `Charger.reset`,
`Charger.start`, and `Charger.stop` prepare typed OCPP 1.6 or 2.0.1 requests
from the selected charger and use the persisted shared-delivery boundary. Their
class-level signatures accept a GWAY-piped `Charger` as the first argument.
Reset is graceful by default; start requires an authorization token; and stop
derives its target from the active local transaction unless an explicit remote
transaction identifier is needed. The Django `charger` command is read-only,
with no mutation verbs, aliases, generic send/get grammar, protocol override,
or payload injection surface.

### Task CCLI-04: Align administration, simulator coverage, and completion evidence

* Intent: Make the initial model-operation outcomes observable and prove the three
  typed operations use the same retained protocol contracts as the WebSocket
  surface.
* Scope: `apps/ocpp/admin/`, `apps/ocpp/simulator/`, model-operation/transport tests,
  `docs/transition/ocpp-protocol-expansion.md`, and `PLAN.md`.
* Constraints: Admin remains an inspection surface; it does not grow a generic
  payload editor. Simulator scenarios act as protocol clients, never as a host
  service. Redact sensitive request/response fields in every renderer and test
  fixture.
* Acceptance Criteria: Each initial operation has success, CallError, timeout,
  disconnect, and version-mismatch coverage where applicable. Admin exposes operation
  target, version, action, timing, and terminal result safely. The simulator
  exercises all inbound actions, and plan completion distinguishes explicit
  model-operation delivery from prohibited automation.
* Verification Commands: `.venv/bin/python manage.py test tests.ocpp`; `.venv/bin/python manage.py check --fail-level ERROR`; `.venv/bin/python manage.py makemigrations --check --dry-run`; `python -m ruff format --check --config pyproject.toml apps arthexis tests`; `git diff --check`.
* Out of Scope: Reconciliation, production service installation, recurring
  charger jobs, and external API integration.
* Depends on: CCLI-03.
* Blocks: OCPPX-07, V200-04, V200-05.
* Parallel-safe: no.
* Risk/Rollback: medium; primary failure is unsafe result disclosure or
  incomplete evidence. Roll back the renderer or scenario independently while
  retaining persisted operation records.

**Implementation record (2026-09-19):** Complete. `ProtocolOperation` admin
now presents target, protocol, action, timing, status, and a safe terminal
summary while excluding request and response payloads. The in-process protocol
client exercises every retained inbound action in both versions. The three
initial controls have success, CallError, timeout, disconnect, and applicable
version-mismatch coverage; no simulator is installed as a host service.
