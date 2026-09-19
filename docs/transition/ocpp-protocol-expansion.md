# OCPP protocol expansion plan

## Decision and boundary

Arthexis 2.0 will transfer every OCPP operation supported by the frozen 1.x
source across both retained protocol surfaces: OCPP 1.6 and OCPP 2.0.1. For
each operation, 2.0 must parse and validate inbound calls, emit its required
response or call-error, and be able to compose and correlate the corresponding
outbound call where that direction is supported.

This does **not** transfer operational orchestration. Arthexis 2.0 will not
schedule, trigger, or automatically execute charger operations for firmware,
logs, diagnostics, configuration rollout, reservations, or maintenance. Those
messages remain protocol-capable: an explicit administrator, supported command,
or future integration can emit them and receive their results. Inbound status
and notification calls remain accepted and persisted when their retained domain
contract requires it.

The current six-action runtime is a bootstrap implementation, not the protocol
completion claim. The expansion below replaces its direct action methods with
the package architecture before adding further actions.

## Frozen supported-action matrix

The implementation gate is the frozen 1.x action registry and its matching
outbound call implementations. A task may add an action only after adding it
to this matrix with its direction, version, persisted resource, and test case.

### OCPP 1.6

| Direction | Actions |
| --- | --- |
| Charge point to CSMS | `Authorize`, `BootNotification`, `DataTransfer`, `DiagnosticsStatusNotification`, `FirmwareStatusNotification`, `Heartbeat`, `MeterValues`, `StartTransaction`, `StatusNotification`, `StopTransaction` |
| CSMS to charge point | `CancelReservation`, `ChangeAvailability`, `ChangeConfiguration`, `ClearChargingProfile`, `DataTransfer`, `GetCompositeSchedule`, `GetConfiguration`, `GetDiagnostics`, `GetLocalListVersion`, `RemoteStartTransaction`, `RemoteStopTransaction`, `Reset`, `ReserveNow`, `SendLocalList`, `SetChargingProfile`, `TriggerMessage`, `UnlockConnector`, `UpdateFirmware` |

### OCPP 2.0.1

| Direction | Actions |
| --- | --- |
| Charging station to CSMS | `Authorize`, `BootNotification`, `ClearedChargingLimit`, `CostUpdated`, `DataTransfer`, `FirmwareStatusNotification`, `Get15118EVCertificate`, `GetCertificateStatus`, `Heartbeat`, `LogStatusNotification`, `MeterValues`, `NotifyChargingLimit`, `NotifyCustomerInformation`, `NotifyDisplayMessages`, `NotifyEVChargingNeeds`, `NotifyEVChargingSchedule`, `NotifyEvent`, `NotifyMonitoringReport`, `NotifyReport`, `PublishFirmwareStatusNotification`, `ReportChargingProfiles`, `ReservationStatusUpdate`, `SecurityEventNotification`, `SignCertificate`, `StatusNotification`, `TransactionEvent` |
| CSMS to charging station | `CancelReservation`, `CertificateSigned`, `ChangeAvailability`, `ClearChargingProfile`, `ClearDisplayMessage`, `ClearVariableMonitoring`, `CustomerInformation`, `DataTransfer`, `DeleteCertificate`, `GetBaseReport`, `GetCompositeSchedule`, `GetDisplayMessages`, `GetInstalledCertificateIds`, `GetLocalListVersion`, `GetLog`, `GetReport`, `GetVariables`, `InstallCertificate`, `PublishFirmware`, `RequestStartTransaction`, `RequestStopTransaction`, `ReserveNow`, `Reset`, `SendLocalList`, `SetChargingProfile`, `SetDisplayMessage`, `SetMonitoringBase`, `SetMonitoringLevel`, `SetVariableMonitoring`, `SetVariables`, `TriggerMessage`, `UnlockConnector`, `UpdateFirmware` |

`Authorize`, `BootNotification`, `Heartbeat`, `StatusNotification`, and
`MeterValues` are version-specific payload contracts even when their action
names match. A complete operation means both its supported versions and
directions have contract coverage.

## Package architecture

The OCPP app is split by protocol boundary and responsibility. No new source
file should normally exceed 400 lines; 800 lines is a hard ceiling. An action
family must be split before it approaches that ceiling. Each individual action
codec/handler should target 120–250 lines.

```text
apps/ocpp/
  models/
    assets.py              # StationModel, Charger, Connector
    sessions.py            # OcppTransaction, MeterValue
    operations.py          # outbound correlation and retained operation records
    __init__.py            # stable model exports only
  transport/
    consumer.py            # thin Channels consumer, <= 160 lines
    connection.py          # authentication, registration, lifecycle
    dispatch.py            # Call / CallResult / CallError frame dispatch
    sender.py              # outbound send and timeout wiring
  protocol/
    frames.py              # common OCPP frame types and validation
    errors.py              # protocol error translation
    correlation.py         # unique IDs, pending calls, completion
    registry.py            # version/direction/action lookup only
    v16/
      inbound/             # session.py, metering.py, notifications.py, transfer.py
      outbound/            # control.py, configuration.py, reservations.py, profiles.py, operations.py
    v201/
      inbound/             # session.py, reporting.py, notifications.py, certificates.py
      outbound/            # control.py, variables.py, reporting.py, display.py, certificates.py, operations.py
  domain/
    authorization.py       # cards and accounts boundary
    sessions.py            # transaction and meter persistence
    configuration.py       # retained configuration/variable records
    reservations.py
    profiles.py
    notifications.py
    certificates.py
  admin/
    assets.py
    sessions.py
    operations.py
  simulator/
    scenarios.py
    transport.py
    assertions.py
```

`transport` never embeds action business logic. `protocol` never imports Django
admin or Channels. `domain` owns database writes and exposes small typed
services. The registry is declarative and maps one `(version, direction,
action)` key to one handler; it does not become a second consumer.

The initial `apps/ocpp/models.py`, `consumers.py`, `tasks.py`, and `routes.py`
are transitional entrypoints. OCPPX-02 moves their contents into the packages
above, retaining only thin compatibility imports where internal call sites need
them during the refactor. No 1.x module is copied into 2.0.

## Task OCPPX-01: Freeze the protocol contract matrix

* Intent: Turn the supported-action matrix into executable, versioned 2.0 contracts before adding handlers.
* Scope: `docs/transition/ocpp-protocol-expansion.md`, `apps/ocpp/protocol/registry.py`, `tests/ocpp/test_action_matrix.py`.
* Constraints: Include both direction and protocol version for every action. Treat an unsupported standard action as out of scope only when it was not supported by frozen 1.x. Do not add charger-operation scheduling.
* Acceptance Criteria: Every matrix action has one registry entry, declared payload contract, response/call-error contract, persistence owner, and at least one version/direction test.
* Verification Commands: `.venv/bin/python manage.py test tests.ocpp.test_action_matrix`; `.venv/bin/python manage.py check --fail-level ERROR`; `python -m ruff check --config pyproject.toml apps arthexis tests`.
* Out of Scope: Handler implementation beyond the registry, Celery schedules, and reconciliation.
* Depends on: V200-03.
* Blocks: OCPPX-02 through OCPPX-07.
* Parallel-safe: no.
* Risk/Rollback: medium; primary failure is an incomplete action inventory. Roll back by correcting the matrix before any action implementation is accepted.

**Implementation record (2026-09-17):** Complete. The 2.0 registry has 87
unique `(version, direction, action)` contracts. Each declares its request,
response, call-error, and persistence boundary, and tests assert the full
matrix. It intentionally does not dispatch actions yet.

## Task OCPPX-02: Establish transport, frame, and correlation packages

* Intent: Replace the bootstrap consumer with a version-aware transport that safely handles Call, CallResult, and CallError frames.
* Scope: `apps/ocpp/transport/`, `apps/ocpp/protocol/frames.py`, `errors.py`, `correlation.py`, `registry.py`, `routes.py`, `arthexis/asgi.py`, and transport tests.
* Constraints: Keep the Channels consumer thin; negotiate supported OCPP subprotocols; authenticate and register connections; bound pending-call lifetime; never let event publication or persistence failure corrupt a protocol response.
* Acceptance Criteria: Both OCPP 1.6 and 2.0.1 connect through explicit negotiation; inbound calls dispatch by registry; outbound calls correlate success, error, timeout, and disconnect outcomes; malformed frames receive protocol call-errors.
* Verification Commands: `.venv/bin/python manage.py test tests.ocpp.test_transport`; `.venv/bin/python manage.py check --fail-level ERROR`; `python -m ruff format --check --config pyproject.toml apps arthexis tests`.
* Out of Scope: Individual action-family behavior and operational orchestration.
* Depends on: OCPPX-01.
* Blocks: OCPPX-03 through OCPPX-07.
* Parallel-safe: no.
* Risk/Rollback: high; primary failure is breaking live frame semantics. Roll back to the bootstrap consumer while retaining the action matrix and model migrations.

**Implementation record (2026-09-17):** Complete. `transport/` now owns
subprotocol negotiation, pre-registered charger authentication, frame dispatch,
and outbound correlation. The bootstrap OCPP 1.6 core actions are delegated
through the registry; OCPP 2.0.1 currently negotiates and dispatches to a safe
not-implemented call-error until its action-family task adds handlers.

## Task OCPPX-03: Rebuild retained OCPP persistence by domain package

* Intent: Add only the models needed by the action matrix and move current OCPP models into small domain-owned packages.
* Scope: `apps/ocpp/models/`, `apps/ocpp/domain/`, `apps/ocpp/admin/`, migrations, and model/admin tests.
* Constraints: Preserve charger, connector, transaction, meter, configuration/variable, reservation, charging-profile, monitoring, notification, certificate, firmware/log, and protocol-operation records only where a matrix action needs them. No NetworkManager, host TLS, generic media, or scheduler models.
* Acceptance Criteria: Every persisted matrix action identifies an owning model/service; migrations are new 2.0 migrations; admins expose retained records without host-management controls.
* Verification Commands: `.venv/bin/python manage.py makemigrations --check --dry-run`; `.venv/bin/python manage.py migrate`; `.venv/bin/python manage.py test tests.ocpp.test_models`.
* Out of Scope: Legacy import and executing charger work on a schedule.
* Depends on: OCPPX-02.
* Blocks: OCPPX-04 through OCPPX-07 and V200-04.
* Parallel-safe: no.
* Risk/Rollback: high; primary failure is a model that cannot represent a retained payload. Roll back the isolated 2.0 migration before reconciliation begins.

**Implementation record (2026-09-17):** Complete. `models/`, `admin/`, and
`domain/` now separate asset, session, operation, configuration, reservation,
profile, notification, monitoring, certificate, and firmware/log/diagnostic
status responsibilities. The new 2.0 migration stores no certificate material,
only retained certificate metadata and fingerprints.

## Task OCPPX-04: Implement all OCPP 1.6 directions

* Intent: Transfer every frozen supported OCPP 1.6 inbound and outbound action through the common transport and domain services.
* Scope: `apps/ocpp/protocol/v16/`, matching `domain/` services, explicit administrator/command emitters, simulator scenarios, and OCPP 1.6 tests.
* Constraints: Group actions by session, metering, configuration/control, reservations, profiles, and operational notifications; split a group into another module before 400 lines. Outbound messages require explicit administrator, command, or integration invocation—no Beat schedule or triggered charger automation.
* Acceptance Criteria: Every OCPP 1.6 matrix action parses, validates, responds or emits correctly, persists its retained result when applicable, and covers call-result/call-error behavior. `MeterValues` and full transaction accounting are included.
* Verification Commands: `.venv/bin/python manage.py test tests.ocpp.v16`; `.venv/bin/python manage.py test tests.ocpp.test_simulator`; `python -m ruff check --config pyproject.toml apps arthexis tests`.
* Out of Scope: OCPP 2.0.1 handlers, scheduled charger operations, and reconciliation.
* Depends on: OCPPX-03.
* Blocks: OCPPX-06, V200-04.
* Parallel-safe: no.
* Risk/Rollback: high; primary failure is incompatible station behavior. Roll back the individual action family and retain recorded protocol-operation data for diagnosis.

**Implementation record (2026-09-17):** Complete. The OCPP 1.6 inbound
handlers now cover authorization, boot, heartbeat, status, transactions, meter
values, data transfer, diagnostics status, and firmware status. All 18 OCPP
1.6 outbound matrix actions validate an explicit payload and can be emitted to
an active connection through `emit_v16_operation`, which persists successful,
errored, timed-out, and disconnected outcomes. No handler or task schedules a
charger command.

## Task OCPPX-05: Implement all OCPP 2.0.1 directions

* Intent: Transfer every frozen supported OCPP 2.0.1 inbound and outbound action, including reporting, variable, display, charging-limit, and certificate surfaces.
* Scope: `apps/ocpp/protocol/v201/`, matching `domain/` services, explicit emitters, simulator scenarios, and OCPP 2.0.1 tests.
* Constraints: Do not pretend a same-named 1.6 handler is compatible with its 2.0.1 payload. Keep certificate material secret-safe in logs, events, tests, and reconciliation artifacts. Do not schedule or automatically trigger charger work.
* Acceptance Criteria: Every OCPP 2.0.1 matrix action has independent payload and response contracts, correlation coverage, persistence where retained, and success/error tests.
* Verification Commands: `.venv/bin/python manage.py test tests.ocpp.v201`; `.venv/bin/python manage.py test tests.ocpp.test_transport`; `.venv/bin/python manage.py makemigrations --check --dry-run`.
* Out of Scope: OCPP 1.6 changes except shared transport fixes, operational orchestration, and legacy import.
* Depends on: OCPPX-03.
* Blocks: OCPPX-06, V200-04.
* Parallel-safe: yes, after OCPPX-03 if it does not change shared transport/domain contracts.
* Risk/Rollback: high; primary failure is cross-version payload conflation. Roll back the affected 2.0.1 action module and migration independently.

**Implementation record (2026-09-17):** Complete. OCPP 2.0.1 action families
now live in `v201/inbound/` and `v201/outbound/`, rather than sharing 1.6
handlers. All 26 inbound matrix actions route through the WebSocket dispatcher
and persist their retained state or acknowledgement; all 33 outbound actions
validate an explicit payload and use `emit_v201_operation` for correlation and
operation-record persistence. Certificate requests store only a fingerprint and
metadata, never supplied certificate or CSR material. No scheduler, task, or
handler initiates charger work.

## Task OCPPX-06: Complete explicit emitters, administration, and simulator coverage

The refined command grammar, shared-delivery prerequisite, and task breakdown
are in [charger-command-surface.md](charger-command-surface.md). That plan is
authoritative for management-command scope: `charger` is the sole command,
protocol versions are inferred from charger configuration, and generic payload
injection is prohibited.

* Intent: Make the initial explicit operations usable and diagnosable without reintroducing autonomous charger operations.
* Scope: `apps/ocpp/admin/`, `apps/ocpp/management/commands/`, `apps/ocpp/simulator/`, event integration, and operator-facing developer documentation.
* Constraints: GWAY exposes explicit reset, start, and stop as `Charger` model
  operations; Django management commands remain limited to app-wide work. All
  other retained outbound actions remain available to programmatic integrations.
  Admin and model operations show pending, completed, errored, and timed-out
  outcomes without secrets. Simulator scenarios remain
  protocol-client behavior, not a host service.
* Acceptance Criteria: A selected `Charger` can explicitly emit reset, start,
  and stop through GWAY across both retained versions; the simulator exercises
  every inbound action; protocol-operation records report correlation outcomes;
  action tests cover success, call-error, timeout, and disconnect paths.
* Verification Commands: `.venv/bin/python manage.py test tests.ocpp`; `.venv/bin/python manage.py check --fail-level ERROR`; `git diff --check`.
* Out of Scope: Celery/Beat workflows that send charger operations, GWAY service installation, and database reconciliation.
* Depends on: OCPPX-04, OCPPX-05.
* Blocks: OCPPX-07, V200-04, V200-05.
* Parallel-safe: no.
* Risk/Rollback: medium; primary failure is an emitter that obscures its target or outcome. Roll back the isolated command/admin action while preserving protocol records.

**Implementation record (2026-09-19):** Complete. The read-only fleet report,
GWAY-ingested `Charger` operations, safe administration view, and in-process
protocol-client scenarios cover the initial explicit-control boundary without
adding a scheduler or a GWAY host service.

## Task OCPPX-07: Protocol completion gate

* Intent: Prove matrix completeness before legacy data reconciliation or clone lifecycle work proceeds.
* Scope: action-matrix report, support documentation, full protocol test suite, and `PLAN.md` completion record.
* Constraints: Completion means every listed action and both retained versions/directions are covered; a passing health endpoint is insufficient. Retired operational orchestration must remain absent from schedules and action handlers.
* Acceptance Criteria: The matrix report has no unimplemented entries; full simulator and correlation suites pass; OCPP-only schedules contain no charger-command dispatch; remaining exclusions are documented and approved.
* Verification Commands: `.venv/bin/python manage.py test tests.ocpp`; `.venv/bin/python manage.py makemigrations --check --dry-run`; `python -m ruff check --config pyproject.toml apps arthexis tests`; `python -m ruff format --check --config pyproject.toml apps arthexis tests`.
* Out of Scope: Reconciliation implementation, production lifecycle deployment, and GWAY infrastructure.
* Depends on: OCPPX-06.
* Blocks: OCPPX-08, V200-04, V200-05.
* Parallel-safe: no.
* Risk/Rollback: medium; primary failure is declaring protocol support from partial tests. Roll back the completion record and retain the matrix as the remaining-work source.

**Implementation record (2026-09-19):** Complete. `manage.py ocpp_matrix`
reports all 87 frozen contracts as implemented from their real handler or
validator surfaces. The OCPP 1.6 and 2.0.1 simulator scenarios exercise every
retained inbound action, correlation outcomes are covered, and a test prevents
OCPP schedules from dispatching charger operations. Reconciliation, lifecycle
deployment, and GWAY infrastructure remain excluded.

## Task OCPPX-08: Enroll new chargers and configure authorization policy

* Intent: Restore local charger enrollment at first authenticated connection
  and make authorization behavior explicit per charger.
* Scope: `apps/ocpp/models/assets.py`, an OCPP migration,
  `apps/ocpp/transport/connection.py`, `apps/ocpp/services/authorization.py`,
  both versioned inbound authorization/session handlers, OCPP admin,
  `tests/ocpp/`, and transition/operator documentation.
* Constraints: An unknown charger identity is created only after retained
  subprotocol negotiation and a verified enrollment credential; a missing or
  invalid credential must not create a record. Never log or return enrollment
  secrets. New chargers default to active, locally managed, and `open`
  authorization: any non-empty protocol-valid card identifier is accepted,
  while the authorization attempt remains auditable and an unassigned session
  is not attributed to an account. An administrator can set `restricted`
  authorization, which accepts only active matching logical cards or accounts.
  Apply the policy consistently to OCPP 1.6 `Authorize`/`StartTransaction` and
  OCPP 2.0.1 `Authorize`/`TransactionEvent`. Keep modules below 400 lines and
  do not create a generic remote-control command, scheduled operation, or
  unauthenticated enrollment path.
* Acceptance Criteria: A first valid enrollment connection atomically creates
  one charger and proceeds through the normal connection lifecycle; a repeated
  connection resolves that same record. Failed enrollment leaves no charger
  record. Default open mode returns the version-correct accepted authorization
  response for opaque non-empty identifiers, records the decision, and permits
  the corresponding transaction flow. Restricted mode returns the version-
  correct rejection for unknown identifiers and accepts active matching cards
  or accounts. Admin exposes the policy and enrollment state without exposing
  secret material. The model change has a new 2.0 migration.
* Verification Commands: `.venv/bin/python manage.py makemigrations --check
  --dry-run`; `.venv/bin/python manage.py migrate`; `.venv/bin/python manage.py
  test run -- tests.ocpp tests.test_runtime_behavior`; `.venv/bin/python manage.py
  check --fail-level ERROR`; `python -m ruff check --config pyproject.toml apps
  arthexis tests`; `python -m ruff format --check --config pyproject.toml apps
  arthexis tests`; `git diff --check`.
* Out of Scope: Unauthenticated public registration, a global open-auth switch,
  automatic account creation or billing attribution, automatic start on plug,
  remote control scheduling, and legacy database reconciliation.
* Depends on: OCPPX-07.
* Blocks: V200-04, V200-05.
* Parallel-safe: no.
* Risk/Rollback: high; primary failure is enrolling an unintended charger or
  accepting a card contrary to the configured policy. Roll back the isolated
  enrollment/auth-policy migration and service changes; retain the prior
  registered-charger authentication behavior until corrected.

**Implementation record (2026-09-19):** Complete. OCPPX-07's final
protocol-completion gate has passed. `ARTHEXIS_OCPP_ENROLLMENT_TOKEN_HASH` enables an
unknown identity to enroll only when it presents a matching Basic credential;
the enrolled record retains a hash of that credential for subsequent
connections. A disabled or invalid enrollment configuration creates nothing.
`Charger.authorization_mode` defaults to `open`, while the admin can select
`restricted`. Both OCPP 1.6 and OCPP 2.0.1 authorization and transaction-start
paths return the corresponding accepted or invalid response and retain an
auditable authorization attempt without assigning an open-policy session to an
account.
