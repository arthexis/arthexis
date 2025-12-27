# OCPP 1.6 User Manual

This manual documents how the Arthexis control system implements each Open Charge Point Protocol (OCPP) 1.6 call that is currently supported. The focus is on the behaviour of the WebSocket consumer that represents our central system (CSMS) and the HTTP endpoints that emit CSMS initiated calls.

## Charge point → CSMS calls

### BootNotification
When a charge point connects it immediately sends a `BootNotification` request. The CSMS replies with the current UTC timestamp, a 300 second heartbeat interval, and an `Accepted` status. No persistence is performed beyond registering the live connection for logging and monitoring purposes.【F:ocpp/consumers.py†L1274-L1280】

### Heartbeat
Heartbeat requests reuse the same UTC timestamp response body. Each heartbeat records the reception time on the associated `Charger` row so the admin UI can surface the last communication time of the device.【F:ocpp/consumers.py†L1282-L1288】

### StatusNotification
Status notifications update both the aggregate (connector-less) `Charger` row and any connector specific row. The handler stores the status, error code, vendor info, and timestamp, mirrors those values on the in-memory objects, and logs the processed payload. It also derives an availability state (`Operative` or `Inoperative`) from the status text and persists the effective availability timestamp for downstream dashboards.【F:ocpp/consumers.py†L1290-L1356】【F:ocpp/models.py†L110-L188】

### Authorize
Authorization requests are resolved against active `CustomerAccount` records that have an allowed RFID credential. When a charger enforces RFID checks we only accept the transaction if the associated account is authorised. Otherwise the charger responds with `Accepted` regardless of the RFID contents.【F:ocpp/consumers.py†L278-L310】【F:ocpp/consumers.py†L1357-L1370】

### MeterValues
Meter value payloads are parsed into `MeterValue` rows tied to the active transaction. The handler maps common measurands (energy, voltage, current, temperature, state-of-charge) into dedicated fields, updates transaction start/stop readings when the context marks a begin or end event, infers the initial meter start when available, and records the charger temperature. Each reading is stored with its timestamp and connector so historical analysis retains the original sampling context.【F:ocpp/consumers.py†L437-L545】

### DiagnosticsStatusNotification
Diagnostics updates synchronise the latest status, timestamp, and upload location across the active connector specific `Charger` record and its aggregate counterpart. A succinct log entry captures the status label and location for later review.【F:ocpp/consumers.py†L1378-L1437】

### StartTransaction
A `StartTransaction` call may arrive with an optional RFID. The CSMS looks up the corresponding account, automatically creates the RFID entry if it is new, and verifies authorisation when the charger requires RFID checks. Accepted transactions create a `Transaction` row, start a live session log, broadcast periodic consumption summaries via the NetMessage system, and return the transaction identifier with `idTagInfo.status` set to `Accepted`. Rejected transactions respond with `Invalid` authorisation status without altering persistence.【F:ocpp/consumers.py†L278-L310】【F:ocpp/consumers.py†L1438-L1479】【F:ocpp/consumers.py†L663-L672】

### StopTransaction
`StopTransaction` finalises the active transaction, ensuring a placeholder exists if the charger references an unknown identifier. The handler stores the meter stop reading and timestamp, stops consumption broadcasting, ends the session log, and responds with `Accepted`. This guarantees consistent transaction state even when the stop arrives without a preceding start message.【F:ocpp/consumers.py†L1480-L1509】【F:ocpp/consumers.py†L578-L608】

### FirmwareStatusNotification
Firmware status notifications persist the reported status, optional info string, and timestamp on all `Charger` identities associated with the connection. Each update is logged so operators can trace firmware upgrade progress.【F:ocpp/consumers.py†L547-L576】【F:ocpp/consumers.py†L1511-L1549】

### DataTransfer
Incoming `DataTransfer` requests are written to the `DataTransferMessage` table so every payload, vendor identifier, and follow-up status is auditable. The consumer stores the raw request with a `cp_to_csms` direction marker, invokes any vendor-specific handler, and records the final status or error metadata alongside the response timestamp before returning the call result to the charger.【F:ocpp/consumers.py†L1039-L1126】【F:ocpp/models.py†L842-L870】

## CSMS → charge point calls
Outgoing calls originate from the control endpoint that proxies UI and admin actions to the live WebSocket session.

### RemoteStartTransaction
A remote start request requires an `idTag` and optionally a connector id or charging profile. The view validates the payload, injects defaults from the current connector context, and asynchronously sends the `RemoteStartTransaction` message to the active connection.【F:apps/ocpp/views/actions.py†L203-L259】

### RemoteStopTransaction
Remote stop requests must target an active transaction. The view fetches the current session from the in-memory store and, if present, sends `RemoteStopTransaction` with the transaction identifier back to the charger.【F:apps/ocpp/views/actions.py†L168-L199】

### Reset
Reset commands always request a soft reset and are dispatched as asynchronous WebSocket sends. Additional metadata is not persisted because reset responses do not currently update model state.【F:apps/ocpp/views/actions.py†L629-L657】

### ChangeAvailability
Change availability requests enforce `Operative`/`Inoperative` validation, normalise the connector id, and send the `ChangeAvailability` call with a unique message id. The view registers pending-call metadata so the WebSocket consumer can reconcile the eventual response, and resets the request tracking fields on the affected `Charger` record to indicate a new change is in progress.【F:apps/ocpp/views/actions.py†L327-L372】【F:ocpp/store.py†L1-L138】【F:ocpp/models.py†L116-L166】

### DataTransfer
Operators can issue a `DataTransfer` via the control endpoint by providing the vendor id, optional message id, and payload data. The view validates the request, persists a `DataTransferMessage` row flagged as `csms_to_cp`, sends the call to the charger, and registers a pending call keyed to that record. When the charger replies, the consumer matches the message id, updates the stored status, response data, or error details, and timestamps the outcome so outbound diagnostics remain traceable.【F:apps/ocpp/views/actions.py†L579-L625】【F:ocpp/consumers.py†L674-L826】【F:ocpp/models.py†L842-L870】

### GetConfiguration
Admins can invoke `GetConfiguration` from the charger changelist action or the OCPP control endpoint. Both surfaces build a list of requested keys (or leave it empty to fetch everything), allocate a unique message id, log the outbound payload in the charger log, and register a pending call that tracks charger/connector scope, request time, and the eventual response.【F:apps/ocpp/admin/charger.py†L910-L947】【F:apps/ocpp/views/actions.py†L46-L89】【F:ocpp/store.py†L662-L717】 The store schedules a five-second timeout that writes a log entry if the device never replies, helping operators distinguish unsupported operations from transient connectivity issues.【F:apps/ocpp/store.py†L675-L698】

When a charger responds, the consumer logs the raw response JSON, persists the configuration snapshot (including unknown keys) to `ChargerConfiguration`, and refreshes the in-memory `Charger` references so subsequent UI reads show the latest values.【F:apps/ocpp/call_result_handlers.py†L565-L594】【F:apps/ocpp/consumers.py†L1188-L1331】【F:apps/ocpp/models/charger_configuration.py†L10-L105】 Unknown-key entries are stored alongside the configuration keys, and the admin UI displays the most recent snapshot on the charger detail page so operators can audit what the device returned.

### Handling responses
When a charger replies to `ChangeAvailability`, the consumer matches the message id against the pending-call registry. Call results mark the request as accepted and update the requested state, while call errors flag the request as rejected and capture the error details. The stored status feeds the admin interface so operators can track whether the requested availability state took effect.【F:ocpp/consumers.py†L674-L826】【F:ocpp/consumers.py†L827-L906】

### TriggerMessage follow-ups
`TriggerMessage` requests are sent from the admin actions UI and the generic control endpoint. Successful responses register a follow-up entry in the in-memory store keyed by charger serial, target action, and connector so the consumer can annotate the next related message with a log entry when it arrives.【F:apps/ocpp/admin/charger.py†L868-L903】【F:apps/ocpp/views/actions.py†L819-L859】【F:apps/ocpp/call_result_handlers.py†L597-L628】【F:apps/ocpp/store.py†L719-L736】 When the charge point later emits the triggered notification (for example, `StatusNotification`), the consumer consumes the stored follow-up and logs a “TriggerMessage follow-up received” entry against the appropriate connector log, giving operators a clear request/response chain.【F:apps/ocpp/consumers.py†L1775-L1810】

## Session logging and consumption telemetry
The CSMS keeps per-session logs and broadcasts consumption summaries for active transactions. When a transaction starts, the consumer creates a NetMessage broadcast and schedules periodic refreshes. Stopping the transaction cancels the task and closes the session log to prevent stale updates from leaking after the charge point disconnects.【F:ocpp/consumers.py†L591-L672】【F:ocpp/consumers.py†L1438-L1506】

## In-memory coordination
All active WebSocket connections, transactions, logs, and pending CSMS calls are tracked in the shared `ocpp.store` module. Helper functions provide consistent identity keys per charger/connector pair, enforce a two-connection-per-IP rate limit, and expose lookups used by both the WebSocket consumer and the control endpoint. This lightweight store allows the manual operations above to find the correct charger session instantly without hitting the database.【F:ocpp/store.py†L1-L116】

### Timeout and error handling
Pending-call metadata is stored in-memory and mirrored to Redis so responses can be reconciled even after reconnects. Each outgoing call registers an event handle and an optional timeout timer; when a timeout fires, the scheduler adds a charger-log entry and marks the request as notified so duplicate alerts are suppressed.【F:apps/ocpp/store.py†L633-L717】 When the consumer records a result or error (including OCPP call errors), it clears timers, signals waiting threads, and persists the payload and error context, ensuring admin dashboards and logs accurately reflect both success and failure paths.【F:apps/ocpp/store.py†L620-L659】【F:apps/ocpp/call_result_handlers.py†L565-L628】【F:apps/ocpp/call_error_handlers.py†L343-L378】 The request/response lifecycle therefore surfaces clearly across the WebSocket logs, pending-call registries, and charger detail views.
