# OCPP 1.6 User Manual

This manual documents how the Arthexis control system implements each Open Charge Point Protocol (OCPP) 1.6 call that is currently supported. The focus is on the behaviour of the WebSocket consumer that represents our central system (CSMS) and the HTTP endpoints that emit CSMS initiated calls.

## Charge point → CSMS calls

### BootNotification
When a charge point connects it immediately sends a `BootNotification` request. The CSMS replies with the current UTC timestamp, a 300-second heartbeat interval, and an `Accepted` status. No persistence is performed beyond registering the live connection for logging and monitoring purposes.

### Heartbeat
Heartbeat requests reuse the same UTC timestamp response body. Each heartbeat records the reception time on the associated `Charger` row so the admin UI can surface the last communication time of the device.

### StatusNotification
Status notifications update both the aggregate (connector-less) `Charger` row and any connector-specific row. The handler stores the status, error code, vendor info, and timestamp, mirrors those values on the in-memory objects, and logs the processed payload. It also derives an availability state (`Operative` or `Inoperative`) from the status text and persists the effective availability timestamp for downstream dashboards.

### Authorize
Authorization requests are evaluated through the charger authorization policy (`strict`, `allowlist`, or explicit `open` insecure compatibility mode) with a global fallback when unset. `strict` requires an authorised linked account, `allowlist` also permits known allowed unlinked tags, and `open` accepts any token while clearly marking the mode as insecure compatibility behavior. Every decision returns a reason code for auditability.

### MeterValues
Meter value payloads are parsed into `MeterValue` rows tied to the active transaction. The handler maps common measurands (energy, voltage, current, temperature, state-of-charge) into dedicated fields, updates transaction start/stop readings when the context marks a begin or end event, infers the initial meter start when available, and records the charger temperature. Each reading is stored with its timestamp and connector so historical analysis retains the original sampling context.

### DiagnosticsStatusNotification
Diagnostics updates synchronise the latest status, timestamp, and upload location across the active connector-specific `Charger` record and its aggregate counterpart. A succinct log entry captures the status label and location for later review.

### StartTransaction
A `StartTransaction` call may arrive with an optional RFID. The CSMS looks up the corresponding account, evaluates the configured authorization policy, and records a reasoned accepted/rejected decision. RFID rows are still observed for traceability, but tags are only auto-approved/released when the selected policy explicitly allows insecure compatibility auto-enrollment. Accepted transactions create a `Transaction` row, start a live session log, broadcast periodic consumption summaries via the NetMessage system, and return the transaction identifier with `idTagInfo.status` set to `Accepted`. Rejected transactions respond with `Invalid` authorization status and persist rejection metadata for operator review.

### StopTransaction
`StopTransaction` finalises the active transaction, ensuring a placeholder exists if the charger references an unknown identifier. The handler stores the meter stop reading and timestamp, stops consumption broadcasting, ends the session log, and responds with `Accepted`. This guarantees consistent transaction state even when the stop arrives without a preceding start message.

### FirmwareStatusNotification
Firmware status notifications persist the reported status, optional info string, and timestamp on all `Charger` identities associated with the connection. Each update is logged so operators can trace firmware upgrade progress.

### DataTransfer
Incoming `DataTransfer` requests are written to the `DataTransferMessage` table so every payload, vendor identifier, and follow-up status is auditable. The consumer stores the raw request with a `cp_to_csms` direction marker, invokes any vendor-specific handler, and records the final status or error metadata alongside the response timestamp before returning the call result to the charger.

## CSMS → charge point calls
Outgoing calls originate from the control endpoint that proxies UI and admin actions to the live WebSocket session.

### RemoteStartTransaction
A remote start request requires an `idTag` and optionally a connector id or charging profile. The view validates the payload, injects defaults from the current connector context, and asynchronously sends the `RemoteStartTransaction` message to the active connection.

### RemoteStopTransaction
Remote stop requests must target an active transaction. The view fetches the current session from the in-memory store and, if present, sends `RemoteStopTransaction` with the transaction identifier back to the charger.

### Reset
Reset commands always request a soft reset and are dispatched as asynchronous WebSocket sends. Additional metadata is not persisted because reset responses do not currently update model state.

### ChangeAvailability
Change availability requests enforce `Operative`/`Inoperative` validation, normalise the connector id, and send the `ChangeAvailability` call with a unique message id. The view registers pending-call metadata so the WebSocket consumer can reconcile the eventual response, and resets the request tracking fields on the affected `Charger` record to indicate a new change is in progress.

### DataTransfer
Operators can issue a `DataTransfer` via the control endpoint by providing the vendor id, optional message id, and payload data. The view validates the request, persists a `DataTransferMessage` row flagged as `csms_to_cp`, sends the call to the charger, and registers a pending call keyed to that record. When the charger replies, the consumer matches the message id, updates the stored status, response data, or error details, and timestamps the outcome so outbound diagnostics remain traceable.

### GetConfiguration
Admins can invoke `GetConfiguration` from the charger changelist action or the OCPP control endpoint. Both surfaces build a list of requested keys (or leave it empty to fetch everything), allocate a unique message id, log the outbound payload in the charger log, and register a pending call that tracks charger/connector scope, request time, and the eventual response. The store schedules a five-second timeout that writes a log entry if the device never replies, helping operators distinguish unsupported operations from transient connectivity issues.

When a charger responds, the consumer logs the raw response JSON, persists the configuration snapshot (including unknown keys) to `ChargerConfiguration`, and refreshes the in-memory `Charger` references so subsequent UI reads show the latest values. Unknown-key entries are stored alongside the configuration keys, and the admin UI displays the most recent snapshot on the charger detail page so operators can audit what the device returned.

### Handling responses
When a charger replies to `ChangeAvailability`, the consumer matches the message id against the pending-call registry. Call results mark the request as accepted and update the requested state, while call errors flag the request as rejected and capture the error details. The stored status feeds the admin interface so operators can track whether the requested availability state took effect.

### TriggerMessage follow-ups
`TriggerMessage` requests are sent from the admin actions UI and the generic control endpoint. Successful responses register a follow-up entry in the in-memory store keyed by charger serial, target action, and connector so the consumer can annotate the next related message with a log entry when it arrives. When the charge point later emits the triggered notification (for example, `StatusNotification`), the consumer consumes the stored follow-up and logs a “TriggerMessage follow-up received” entry against the appropriate connector log, giving operators a clear request/response chain.

## Session logging and consumption telemetry
The CSMS keeps per-session logs and broadcasts consumption summaries for active transactions. When a transaction starts, the consumer creates a NetMessage broadcast and schedules periodic refreshes. Stopping the transaction cancels the task and closes the session log to prevent stale updates from leaking after the charge point disconnects.

## In-memory coordination
All active WebSocket connections, transactions, logs, and pending CSMS calls are tracked in the shared `ocpp.store` module. Helper functions provide consistent identity keys per charger/connector pair, enforce a default four-connection-per-IP concurrent connection cap (configurable via `OCPP_MAX_CONNECTIONS_PER_IP`), and expose lookups used by both the WebSocket consumer and the control endpoint. This lightweight store allows the manual operations above to find the correct charger session instantly without hitting the database.

### Timeout and error handling
Pending-call metadata is stored in-memory and mirrored to Redis so responses can be reconciled even after reconnects. Each outgoing call registers an event handle and an optional timeout timer; when a timeout fires, the scheduler adds a charger-log entry and marks the request as notified so duplicate alerts are suppressed. When the consumer records a result or error (including OCPP call errors), it clears timers, signals waiting threads, and persists the payload and error context, ensuring admin dashboards and logs accurately reflect both success and failure paths. The request/response lifecycle therefore surfaces clearly across the WebSocket logs, pending-call registries, and charger detail views.

## Operational management commands
Use the unified `ocpp` management command for operational workflows:

- Coverage reports:
  - `.venv/bin/python manage.py ocpp coverage --version 1.6`
  - `.venv/bin/python manage.py ocpp coverage --version 2.0.1`
  - `.venv/bin/python manage.py ocpp coverage --version 2.1`
- Transaction data:
  - `.venv/bin/python manage.py ocpp transactions export <output.json> [--start ... --end ... --chargers ...]`
  - `.venv/bin/python manage.py ocpp transactions import <input.json>`
- Trace tools:
  - `.venv/bin/python manage.py ocpp trace extract [--txn ... --out ... --log ...]`
  - `.venv/bin/python manage.py ocpp trace replay <extract.json>`

Legacy single-purpose commands (`coverage_ocpp16`, `coverage_ocpp201`, `coverage_ocpp21`, `import_transactions`, `export_transactions`, and `ocpp_replay`) have been removed; use the unified `ocpp` command surface above.

### Release migration notes (deprecated command removal)
If your automation still calls legacy entrypoints, update scripts to the canonical `ocpp` subcommands:

| Removed command | Replacement |
| --- | --- |
| `.venv/bin/python manage.py coverage_ocpp16` | `.venv/bin/python manage.py ocpp coverage --version 1.6` |
| `.venv/bin/python manage.py coverage_ocpp201` | `.venv/bin/python manage.py ocpp coverage --version 2.0.1` |
| `.venv/bin/python manage.py coverage_ocpp21` | `.venv/bin/python manage.py ocpp coverage --version 2.1` |
| `.venv/bin/python manage.py import_transactions <input.json>` | `.venv/bin/python manage.py ocpp transactions import <input.json>` |
| `.venv/bin/python manage.py export_transactions <output.json> [--start ... --end ... --chargers ...]` | `.venv/bin/python manage.py ocpp transactions export <output.json> [--start ... --end ... --chargers ...]` |
| `.venv/bin/python manage.py ocpp_replay <extract.json>` | `.venv/bin/python manage.py ocpp trace replay <extract.json>` |


### Release migration notes (forwarder import path removal)
The compatibility shim at `apps.ocpp.forwarder` has been removed.

If integration code still imports the legacy path, switch to `apps.forwarder.ocpp`:

```python
# from apps.ocpp.forwarder import Forwarder, ForwardingSession, forwarder
from apps.forwarder.ocpp import Forwarder, ForwardingSession, forwarder
```

### Release migration notes (simulator import path removal)
The compatibility package at `apps.ocpp.simulator` has been removed.

If external integrations still import the legacy module path, update imports to `apps.simulators`:

| Removed import path | Replacement import path |
| --- | --- |
| `from apps.ocpp.simulator import ChargePointSimulator, SimulatorConfig` | `from apps.simulators import ChargePointSimulator, SimulatorConfig` |

### Release migration notes (EVCS wrapper removal)
The compatibility wrapper module at `apps.ocpp.evcs` has been removed.

External plugin maintainers should update imports to use `apps.simulators.evcs` directly:

| Removed import path | Replacement import path |
| --- | --- |
| `from apps.ocpp.evcs import simulate, simulate_cp, view_simulator, view_cp_simulator` | `from apps.simulators.evcs import simulate, simulate_cp, view_simulator, view_cp_simulator` |
| `from apps.ocpp.evcs import _simulator_status_json, _start_simulator, _stop_simulator, get_simulator_state, parse_repeat` | `from apps.simulators.evcs import _simulator_status_json, _start_simulator, _stop_simulator, get_simulator_state, parse_repeat` |

For shell migration, a direct one-time update can be done with substitutions like:

```bash
# GNU sed
sed -i \
  -e 's|.venv/bin/python manage.py coverage_ocpp16|.venv/bin/python manage.py ocpp coverage --version 1.6|g' \
  -e 's|.venv/bin/python manage.py coverage_ocpp201|.venv/bin/python manage.py ocpp coverage --version 2.0.1|g' \
  -e 's|.venv/bin/python manage.py coverage_ocpp21|.venv/bin/python manage.py ocpp coverage --version 2.1|g' \
  -e 's|.venv/bin/python manage.py import_transactions|.venv/bin/python manage.py ocpp transactions import|g' \
  -e 's|.venv/bin/python manage.py export_transactions|.venv/bin/python manage.py ocpp transactions export|g' \
  -e 's|.venv/bin/python manage.py ocpp_replay|.venv/bin/python manage.py ocpp trace replay|g' \
  path/to/ops-script.sh

# BSD/macOS sed
sed -i '' \
  -e 's|.venv/bin/python manage.py coverage_ocpp16|.venv/bin/python manage.py ocpp coverage --version 1.6|g' \
  -e 's|.venv/bin/python manage.py coverage_ocpp201|.venv/bin/python manage.py ocpp coverage --version 2.0.1|g' \
  -e 's|.venv/bin/python manage.py coverage_ocpp21|.venv/bin/python manage.py ocpp coverage --version 2.1|g' \
  -e 's|.venv/bin/python manage.py import_transactions|.venv/bin/python manage.py ocpp transactions import|g' \
  -e 's|.venv/bin/python manage.py export_transactions|.venv/bin/python manage.py ocpp transactions export|g' \
  -e 's|.venv/bin/python manage.py ocpp_replay|.venv/bin/python manage.py ocpp trace replay|g' \
  path/to/ops-script.sh
```
