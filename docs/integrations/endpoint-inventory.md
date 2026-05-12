# Arthexis Endpoint Documentation Inventory

This inventory tracks HTTP and WebSocket-facing endpoints in the Arthexis suite, grouped by app and focused first on charging operations and external-system integrations.

> **Status legend**: `Complete` = request/response/auth/idempotency/model links captured here, `In progress` = partially captured and needs expansion, `Missing` = endpoint exists but still needs full documentation.

Arthexis should be extended as an integration pivot (apps + models + migrations) rather than with disconnected side systems; this inventory is intended to keep those integration contracts explicit and maintainable.

## 1) `apps.ocpp` (charging operations) — priority: highest

### HTTP endpoints

| Endpoint | Caller | Purpose | Status |
|---|---|---|---|
| `GET /ocpp/chargers/` | Admin/operator UI | Charger fleet state feed (JSON) | Complete |
| `GET /ocpp/chargers/<cid>/` | Admin/operator UI | Charger detail and live transaction info | Complete |
| `POST /ocpp/chargers/<cid>/action/` | Admin/operator UI | Send remote OCPP action (start/stop/reset/etc.) | Complete |
| `POST /ocpp/chargers/<cid>/connector/<connector>/action/` | Admin/operator UI | Connector-scoped action dispatch | Complete |
| `GET /ocpp/c/<cid>/status/` + related chart/log/session/public pages | Admin/operator UI + end users | Public and operator pages around charger status/sessions | Complete |
| `GET /ocpp/firmware/<deployment_id>/<token>/` | Charge point firmware agent | Download firmware package with tokenized URL | Complete |

### WebSocket endpoints

| Endpoint | Caller | Purpose | Status |
|---|---|---|---|
| `ws://<host>/ws/sink/` | Test/simulator tooling | Debug sink that ACKs OCPP Call frames | In progress |
| `ws://<host>/<any>/<cid>/` (catch-all OCPP route) | Charge points | CSMS OCPP 1.6/2.x session | Complete |

### Detailed contract notes (documented now)

#### `GET /ocpp/chargers/`
- **Expected caller**: authenticated admin/operator browser session.
- **AuthN/AuthZ**:
  - Requires an authenticated Django user (`api_login_required`), returns `401` with `{"detail": "authentication required"}` otherwise.
  - Visibility scoped by charger access helpers.
- **Example success**:
```json
{
  "chargers": [
    {
      "charger_id": "SIM-CP-1",
      "connector_id": null,
      "status": "Charging",
      "connected": true,
      "authorization_policy": "rfid_or_account",
      "transaction": {
        "transactionId": 1042,
        "meterStart": 112233,
        "startTime": "2026-03-31T18:01:22+00:00"
      }
    }
  ]
}
```
- **Example error**:
```json
{"detail": "authentication required"}
```
- **Idempotency/retry**: safe read (`GET`), retries are safe.
- **Business models + migrations**:
  - Models: `apps.ocpp.models.Charger`, `apps.ocpp.models.Transaction`.
  - Migration roots: `apps/ocpp/migrations/`.

#### `GET /ocpp/chargers/<cid>/`
- **Expected caller**: authenticated admin/operator browser session.
- **AuthN/AuthZ**: same as list, with per-charger access check.
- **Example success**:
```json
{
  "charger_id": "SIM-CP-1",
  "connector_id": 1,
  "status": "Available",
  "log": ["< [2,\"...\",\"Heartbeat\",{}]"],
  "transaction": null
}
```
- **Example errors**:
```json
{"detail": "authentication required"}
```
```json
{"detail": "not found"}
```
- **Idempotency/retry**: safe read.
- **Business models + migrations**: same as charger list.

#### `POST /ocpp/chargers/<cid>/action/` and connector variant
- **Expected caller**: authenticated admin/operator UI automation.
- **Purpose**: sends CSMS->CP command (`action`) over active OCPP socket and waits for pending-call result.
- **AuthN/AuthZ**:
  - Session auth required (`401` if absent).
  - Charger access required (403/redirect behavior from access helper).
- **Request example**:
```json
{
  "action": "RequestStartTransaction",
  "idToken": "AABBCCDD",
  "evseId": 1,
  "remoteStartId": 9001
}
```
- **Success example**:
```json
{"sent": "[2, \"<message_id>\", \"RequestStartTransaction\", {\"idToken\": {\"idToken\": \"AABBCCDD\", \"type\": \"Central\"}, \"remoteStartId\": 9001, \"evseId\": 1}]"}
```
- **Error examples**:
```json
{"detail": "no connection"}
```
```json
{"detail": "unknown action"}
```
```json
{"detail": "idToken required"}
```
```json
{"detail": "Request start transaction did not receive a response from the charger."}
```
- **Auth token type / scope / expiry behavior**:
  - Uses Django login session cookie (not bearer API token).
  - Scope is effectively user permissions + charger visibility.
  - Session expiry follows Django session backend settings.
- **Idempotency/retry**:
  - **Not idempotent** for state-changing actions (start/stop/reset/profile changes).
  - Caller should retry only on transport-level failures/timeouts with external dedupe keys (`remoteStartId` is an OCPP 2.x strategy; for OCPP 1.6 `RemoteStartTransaction`, rely on transport-layer idempotency and/or a custom dedupe key because no standard `remoteStartId` field exists).
- **Business models + migrations**:
  - Models: `Charger`, `Transaction`, `ChargingProfile`, `ProtocolCall`, plus store-backed pending call metadata.
  - Migrations: `apps/ocpp/migrations/`, `apps/protocols/migrations/`.

#### `GET /ocpp/c/<cid>/` and connector variant
- **Expected caller**: end users and operator UI links.
- **Purpose**: public charger landing page showing the charger state, active session summary, connector navigation, account summary link when applicable, and remote-start affordances for authenticated users.
- **Variants**:
  - `GET /ocpp/c/<cid>/`
  - `GET /ocpp/c/<cid>/connector/<connector>/`
- **AuthN/AuthZ**:
  - Anonymous users may view chargers with no owner scope.
  - If a charger has owner-user or owner-group restrictions, anonymous users are redirected to the login page.
  - Authenticated users may view unrestricted chargers, chargers explicitly assigned to them, chargers assigned to one of their groups, or chargers visible through charge-station-manager/superuser privileges.
  - Authenticated users outside the allowed scope receive a not-found response rather than visibility details.
- **Example success**: HTML page (`200`) containing charger state, connector links, and live-session context.
- **Example errors**:
  - `302` redirect to login for anonymous access to owner-scoped chargers.
  - `404` HTML error for invalid charger serials, invalid connector slugs, missing aggregate chargers, or authenticated users outside the visibility scope.
- **Idempotency/retry**:
  - Retrying is safe for rendered page content.
  - The view may clear stale cached status before rendering.
  - A valid connector-scoped request may initialize that connector row if it does not already exist.
- **Business models + migrations**:
  - Models: `Charger`, `Transaction`, owner user/group relations, live-session store metadata.
  - Migration roots: `apps/ocpp/migrations/`.

#### `GET /ocpp/c/<cid>/status/` and status chart JSON
- **Expected caller**: authenticated end users, charger owners, charge-station managers, and operator UI automation.
- **Purpose**: render a charger status/session page and provide chart data for live or historic sessions.
- **Variants**:
  - `GET /ocpp/c/<cid>/status/`
  - `GET /ocpp/c/<cid>/connector/<connector>/status/`
  - `GET /ocpp/c/<cid>/status/chart/`
  - `GET /ocpp/c/<cid>/connector/<connector>/status/chart/`
  - Legacy status routes: `GET /ocpp/charger/<cid>/status/` and `GET /ocpp/charger/<cid>/connector/<connector>/status/`
- **AuthN/AuthZ**:
  - Requires an authenticated Django session (`login_required`); anonymous users are redirected to login.
  - Visibility follows the same charger owner/group/manager rules as the public charger landing page.
  - The chart JSON endpoint uses the same charger visibility rules and hides denied/missing data behind the same not-found payload.
- **Query parameters**:
  - `session=<transaction_pk>` selects a historic transaction for status/chart rendering.
  - `dates=charger|received` selects timestamp interpretation in the HTML status view; invalid values fall back to `charger`.
- **HTML success**: `200` charger status page with transaction history, non-transaction events, connector links, chart metadata, and operator-only admin links when the user is staff.
- **Chart success**:
```json
{
  "labels": ["2026-04-01T00:00:00+00:00"],
  "datasets": [
    {
      "label": "Connector 1",
      "values": [1.25],
      "connector_id": 1
    }
  ]
}
```
- **Example errors**:
  - `302` redirect to login for anonymous requests.
  - `404` HTML error for missing/denied status pages.
  - Chart JSON returns `404` for denied chargers, missing chargers/connectors, or missing sessions:
```json
{"detail": "Not found."}
```
- **Idempotency/retry**:
  - Retrying is safe for rendered page content and chart reads.
  - Live-session chart values may naturally change while a transaction is active.
  - HTML status views use the connector-aware page helper, so a valid connector-scoped request may initialize that connector row if it does not already exist; chart JSON is read-only and does not create charger rows.
- **Business models + migrations**:
  - Models: `Charger`, `Transaction`, `MeterValue`, charger owner user/group relations, live-session store metadata.
  - Migration roots: `apps/ocpp/migrations/`.

#### `GET /ocpp/c/<cid>/sessions/` and charger log pages
- **Expected caller**: authenticated end users, charger owners, charge-station managers, and operators.
- **Purpose**: search historic charging sessions and inspect or download charger/simulator logs.
- **Variants**:
  - `GET /ocpp/c/<cid>/sessions/`
  - `GET /ocpp/c/<cid>/connector/<connector>/sessions/`
  - `GET /ocpp/log/<cid>/`
  - `GET /ocpp/log/<cid>/connector/<connector>/`
- **AuthN/AuthZ**:
  - Requires an authenticated Django session (`login_required`); anonymous users are redirected to login.
  - Session search and charger logs use charger visibility rules for `type=charger`.
  - Log pages support non-charger log types for authenticated simulator/debug use; those are keyed by the requested identifier rather than a charger ownership check.
- **Query parameters**:
  - Session search: `date=YYYY-MM-DD`, `range=today|yesterday|last7`, and `dates=charger|received`.
  - Log page: `type=charger` by default, `limit=20|40|100|all`, and `download=1` for text download.
- **HTML success**: `200` session search or log page. Invalid session-search dates render an empty result set; invalid log limits fall back to `20`.
- **Download success**:
  - `200 text/plain; charset=utf-8`
  - `Content-Disposition: attachment; filename="<type>-<cid-or-log-key>.log"`
- **Example errors**:
  - `302` redirect to login for anonymous requests.
  - `404` HTML error for invalid charger serials, invalid connector slugs, missing aggregate chargers, or authenticated users outside charger visibility scope.
- **Idempotency/retry**:
  - Retrying is safe for rendered page content and log downloads.
  - Log downloads are repeatable snapshots of the current in-memory or persisted log stream.
  - Connector-scoped HTML requests use the connector-aware page helper, so a valid connector-scoped request may initialize that connector row if it does not already exist.
- **Business models + migrations**:
  - Models: `Charger`, `Transaction`, `MeterValue`, owner user/group relations, OCPP store logs.
  - Migration roots: `apps/ocpp/migrations/`.

#### `GET /ocpp/firmware/<deployment_id>/<token>/`
- **Expected caller**: charge point/EVSE firmware download agent following an `UpdateFirmware` or `PublishFirmware` location URL.
- **Purpose**: serve a firmware payload associated with a specific deployment through a tokenized URL.
- **AuthN/AuthZ**:
  - Does not require a Django login session or bearer token.
  - Authorization is the tuple of `deployment_id`, `download_token`, and optional `download_token_expires_at`.
  - The URL should be treated as a bearer secret and shared only with the target charge point.
- **Success behavior**:
  - Returns firmware bytes with `Content-Type` from the firmware record, defaulting to `application/octet-stream`.
  - Sets `Content-Disposition` to an attachment filename derived from the firmware record.
  - Sets `Content-Length`.
  - Updates `CPFirmwareDeployment.downloaded_at` to the current time.
- **Example success headers**:
```http
HTTP/1.1 200 OK
Content-Type: application/octet-stream
Content-Disposition: attachment; filename="firmware_42"
Content-Length: 1048576
```
- **Example errors**:
  - Invalid deployment id or token: `404` HTML not-found response.
  - Expired token: `403` response with an empty body.
  - Missing firmware record or empty payload: `404` HTML not-found response.
- **Idempotency/retry**:
  - Payload delivery is repeatable while the token remains valid.
  - The download has a metadata side effect (`downloaded_at` is refreshed), so callers should not use it as a pure cache-validation probe.
- **Business models + migrations**:
  - Models: `CPFirmwareDeployment`, `CPFirmware`, associated `Charger`.
  - Migration roots: `apps/ocpp/migrations/`.

#### OCPP WebSocket (catch-all CSMS route)
- **Expected caller**: charge points/EVSE firmware.
- **Purpose**: OCPP 1.6/2.x bidirectional transport.
- **AuthN/AuthZ**:
  - Serial extracted from path/query; invalid serial closes with code `4003`.
  - If charger record requires WebSocket auth, HTTP Basic credentials are required; missing/invalid/unauthorized closes with code `4003`.
  - Admission also gated by suite feature flags for charge-point creation/version support.
- **Frame examples**:
  - CP -> CSMS Call: `[2, "uid-1", "Heartbeat", {}]`
  - CSMS -> CP CallResult: `[3, "uid-1", {"currentTime": "2026-04-01T00:00:00Z"}]`
- **Error behavior**:
  - Invalid serial/auth rejected at connect phase.
  - Pending call timeout on HTTP action bridge surfaces to caller as `504`-style semantic error payload.
- **Idempotency/retry**:
  - CP reconnects are expected and safe; CSMS restores pending call context per charger identity.
  - Message-level retries should follow OCPP semantics (same message IDs only within protocol expectations).
- **Business models + migrations**:
  - `Charger`, `ChargingStation`, `Transaction`, `MeterValue`, protocol-specific models under `apps/ocpp/models/`.

---

## 2) `apps.nodes` (external node-to-node integrations) — priority: highest

### HTTP endpoints

| Endpoint | Caller | Purpose | Status |
|---|---|---|---|
| `GET /nodes/info/` | System integration (visitor/host nodes) | Local node metadata + optional token signature | Complete |
| `POST /nodes/register/` | System integration (node enrollment) | Register/update node identity and capabilities | Complete |
| `POST /nodes/register/enrollment-public-key/` | System integration | One-time enrollment key submission | Complete |
| `POST /nodes/register/proxy/` | Admin/operator | Server-side registration proxy handshake | Complete |
| `POST /nodes/register/telemetry/` | System integration / browser | Registration telemetry ingest | In progress |
| `GET /nodes/list/` | Admin/operator API client | Node directory | In progress |
| `POST /nodes/network/chargers/` | System integration | Export charger state + optional transactions to trusted node | Complete |
| `POST /nodes/network/chargers/forward/` | System integration | Import forwarded charger state/transactions | Complete |
| `POST /nodes/network/chargers/action/` | System integration | Execute delegated remote charger action | Complete |
| `POST /nodes/net-message/` | System integration | Push signed gossip/net messages | In progress |
| `POST /nodes/net-message/pull/` | System integration | Pull pending signed gossip/net messages | In progress |
| `GET /nodes/migration-status/` | Admin/operator | Deferred migration progress status | In progress |
| `GET/POST /nodes/screenshot/` | Admin/operator | Capture and record screenshot artifact | In progress |

### Detailed contract notes (documented now)

#### `GET /nodes/info/`
- **Expected caller**: host/visitor node software during discovery.
- **Purpose**: expose local node identity, addressing, role/features, and optional token signature.
- **AuthN/AuthZ**: public (CORS `*`), no session required.
- **Request example**: `GET /nodes/info/?token=<one-time-token>`
- **Success example**:
```json
{
  "hostname": "suite-host",
  "address": "203.0.113.10",
  "port": 443,
  "mac_address": "aa:bb:cc:dd:ee:ff",
  "public_key": "-----BEGIN PUBLIC KEY-----...",
  "features": ["ocpp-201-charge-point"],
  "token_signature": "base64-signature"
}
```
- **Idempotency/retry**: safe read.
- **Business models + migrations**:
  - Model: `apps.nodes.models.Node`.
  - Migrations: `apps/nodes/migrations/`.

#### `POST /nodes/register/`
- **Expected caller**: node registration client.
- **Purpose**: create/update node identity, version, role, trust and capabilities.
- **AuthN/AuthZ**:
  - Signature-verified flows preferred.
  - Authenticated Django user may override some signature failures under explicit policy.
  - CORS preflight supported (`OPTIONS`).
- **Request example**:
```json
{
  "hostname": "edge-01",
  "address": "198.51.100.20",
  "mac_address": "aa:bb:cc:dd:ee:11",
  "public_key": "-----BEGIN PUBLIC KEY-----...",
  "trusted": true,
  "features": ["rfid-scanner", "ocpp-16-charge-point"]
}
```
- **Success example**:
```json
{"id": 18, "uuid": "c9f9..."}
```
- **Error examples**:
```json
{"detail": "POST required"}
```
```json
{"detail": "Invalid enrollment token"}
```
- **Idempotency/retry**:
  - Semantically idempotent by `mac_address` upsert behavior.
  - Safe to retry on transient transport failure.
- **Business models + migrations**:
  - Models: `Node`, `NodeRole`.
  - Migrations: `apps/nodes/migrations/`.

#### `POST /nodes/network/chargers/` and `/forward/`
- **Expected caller**: trusted peer nodes.
- **Purpose**: exchange charger + transaction snapshots between nodes.
- **AuthN/AuthZ**:
  - Requires `X-Signature` over request body.
  - Requester identity verified against stored node key via `requester` and optional MAC/public key hints.
- **Request example** (`/network/chargers/`):
```json
{
  "requester": "<node-uuid>",
  "requester_mac": "aa:bb:cc:dd:ee:ff",
  "chargers": [{"charger_id": "SIM-CP-1", "connector_id": 1}],
  "include_transactions": true
}
```
- **Success example**:
```json
{
  "chargers": [{"charger_id": "SIM-CP-1", "connector_id": 1}],
  "transactions": {"count": 12}
}
```
- **Error examples**:
```json
{"detail": "signature required"}
```
```json
{"detail": "unknown requester"}
```
- **Idempotency/retry**:
  - `/network/chargers/` is read-like export (retry safe).
  - `/forward/` applies upserts/sync; designed for retriable eventual consistency.
- **Business models + migrations**:
  - `apps.nodes.models.Node`, `apps.ocpp.models.Charger`, transaction/metering models.

#### `POST /nodes/network/chargers/action/`
- **Expected caller**: trusted manager/origin peer node.
- **Purpose**: delegated remote charger actions (`reset`, `remote-stop`, `request-diagnostics`, etc.).
- **AuthN/AuthZ**:
  - Signed requester required.
  - Charger must allow remote actions and be locally managed.
  - Requesting node must match manager/origin constraints.
- **Success example**:
```json
{"status": "ok", "detail": "diagnostics requested", "updates": {}}
```
- **Error examples**:
```json
{"detail": "remote actions disabled"}
```
```json
{"detail": "requester does not manage this charger"}
```
- **Idempotency/retry**:
  - Not universally idempotent (depends on action).
  - Retry only on explicit transient failures; avoid blind retries for start/stop/reset.
- **Business models + migrations**:
  - `Node`, `Charger`, plus OCPP command-related models.

---

## 3) `apps.actions.api` (admin integration endpoint)

| Endpoint | Caller | Purpose | Status |
|---|---|---|---|
| `GET /actions/api/v1/security-groups/` | Authenticated admin/operator | Enumerate current user's group names for UI policy wiring | Complete |

- **AuthN/AuthZ**: Django `login_required`; session expiration follows Django auth/session settings.
- **Example success**: `{"groups": ["Operators", "Billing"]}`
- **Example error**: redirect/unauthorized behavior from login requirement (HTML login redirect in default Django stack).
- **Idempotency/retry**: safe read.
- **Related models/migrations**: Django `auth.Group` (`django.contrib.auth` migrations).

---

## 4) `apps.sites` (auth + webhook)

### HTTP

| Endpoint | Caller | Purpose | Status |
|---|---|---|---|
| `POST /login/passkey/options/` | Browser login flow | Create WebAuthn challenge | In progress |
| `POST /login/passkey/verify/` | Browser login flow | Verify WebAuthn assertion and establish session | In progress |
| `POST /webhooks/whatsapp/` | External system (WhatsApp bridge) | Accept WhatsApp payload and append to chat session | Complete |

#### `POST /webhooks/whatsapp/` detailed
- **Expected caller**: external WhatsApp delivery service.
- **AuthN/AuthZ**:
  - CSRF-exempt due to external origin.
  - Feature-gated by `PAGES_WHATSAPP_ENABLED` and chat bridge feature flag.
- **Request example**:
```json
{
  "from": "+18005551234",
  "message": "Need help with charger SIM-CP-1",
  "display_name": "Field Operator"
}
```
- **Success example**:
```json
{"status": "ok", "session": "<uuid>", "message": 778}
```
- **Error examples**:
```json
{"detail": "Invalid JSON payload."}
```
```json
{"detail": "Missing WhatsApp sender or message body."}
```
- **Idempotency/retry**:
  - Not strictly idempotent; retries can duplicate messages.
  - Integrator should send dedupe identifiers at transport layer if available.
- **Business models + migrations**:
  - `apps.chats.models.ChatSession`, `ChatMessage` and related migrations under `apps/chats/migrations/`.

---

## 5) `apps.cards` (RFID integration)

| Endpoint | Caller | Purpose | Status |
|---|---|---|---|
| `GET/POST /ocpp/rfid/validator/scan/next/` | Operator UI + local systems | Poll/validate RFID scans | In progress |
| `POST /ocpp/rfid/validator/export/` | Trusted peer node | Export RFID registry | In progress |
| `POST /ocpp/rfid/validator/import/` | Trusted peer node | Import/upsert RFID registry | In progress |
| `POST /ocpp/rfid/validator/scan/deep/` | Staff operator | Trigger deep scan mode | In progress |

---

## 6) `apps.video` (streaming WebSocket APIs)

| Endpoint | Caller | Purpose | Status |
|---|---|---|---|
| `ws://<host>/ws/video/<slug>/` | Browser client | MJPEG frame stream | In progress |
| `ws://<host>/ws/video/<slug>/webrtc/` | Browser client | WebRTC signaling | In progress |
| `ws://<host>/ws/video/<slug>/admin/` | Staff UI | Inactive/admin stream access | In progress |
| `ws://<host>/ws/video/<slug>/admin/webrtc/` | Staff UI | Inactive/admin signaling access | In progress |

---

## 7) `apps.odoo` (external ERP integration)

| Endpoint | Caller | Purpose | Status |
|---|---|---|---|
| `GET /odoo/query/<slug>/` | External/business users (tokenized URL) | Public query runner for configured Odoo query object | In progress |

---

## 8) `apps.evergo` (external customer/integration pages)

| Endpoint | Caller | Purpose | Status |
|---|---|---|---|
| `GET /evergo/dashboard/<token>/` | External customer/support | Tokenized dashboard lookup and sync | In progress |
| `GET /evergo/orders/<order_id>/tracking/` | External customer/support | Public tracking page | In progress |
| `GET /evergo/customers/<pk>/` | Authenticated customer-linked user | Customer profile page | In progress |
| `GET /evergo/customers/<pk>/artifacts/<artifact_id>/download/` | Authenticated customer-linked user | Artifact download | In progress |

---

## 9) Remaining app HTTP surfaces

Additional app URL surfaces exist (core, links, docs, features, widgets, meta, shop, tasks, etc.) and are tracked in the completeness checklist page. They are currently marked mostly `Missing` until endpoint-level contracts are documented with examples and auth semantics.
