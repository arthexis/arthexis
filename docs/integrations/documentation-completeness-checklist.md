# Documentation Completeness Checklist (HTTP + WebSocket Endpoints)

Operational audit artifact for endpoint-documentation status across the Arthexis suite.

## Current-state coverage matrix

### Status rubric
- **Documented (current state)**: purpose, caller, request/response (including errors), auth/authorization, idempotency/retry, and model/migration links are documented.
- **Partially documented (current state)**: some contract documentation exists, but at least one required field is missing.
- **Not yet documented (current state)**: endpoint exists but no usable contract documentation is available.

### Charging and external integrations

| App | Endpoint group | Status | Notes |
|---|---|---|---|
| `apps.ocpp` | `/ocpp/chargers/`, `/ocpp/chargers/<cid>/`, `/ocpp/chargers/<cid>/action/` (plus connector-scoped action endpoint variant, e.g. `/ocpp/chargers/<cid>/connector/<connector>/action/`) | Documented (current state) | Primary charging control and state APIs are documented in the inventory page. |
| `apps.ocpp` | OCPP CSMS WebSocket catch-all (`/<...>/<cid>/`) | Documented (current state) | Auth plus retry/idempotency behavior is documented. |
| `apps.ocpp` | Firmware download + public status/log/chart/session pages | Partially documented (current state) | Per-endpoint error payload and auth matrix details are incomplete. |
| `apps.nodes` | `/nodes/info/`, `/nodes/register/`, `/nodes/register/enrollment-public-key/` | Documented (current state) | Enrollment and registration contracts are documented. |
| `apps.nodes` | `/nodes/network/chargers/`, `/nodes/network/chargers/forward/`, `/nodes/network/chargers/action/` | Documented (current state) | Signed peer sync and delegated charger control are documented. |
| `apps.nodes` | `/nodes/net-message/`, `/nodes/net-message/pull/`, `/nodes/register/proxy/`, `/nodes/register/telemetry/` | Partially documented (current state) | Error matrices and replay-policy details are incomplete. |
| `apps.actions.api` | `/actions/api/v1/security-groups/` | Documented (current state) | Caller/auth and payload contract are documented. |
| `apps.sites` | `/webhooks/whatsapp/` | Documented (current state) | Webhook request/response and feature gating are documented. |
| `apps.sites` | `/login/passkey/options/`, `/login/passkey/verify/`, `/ws/pages/chat/` | Partially documented (current state) | Role/scope/session-expiry detail and socket event schema are incomplete. |
| `apps.cards` | RFID scan/import/export endpoints | Partially documented (current state) | Request/response examples for endpoint variants are incomplete. |
| `apps.video` | `/ws/video/...` sockets | Partially documented (current state) | Signaling/frame/error schema documentation is incomplete. |
| `apps.odoo` | `/odoo/query/<slug>/` | Partially documented (current state) | Auth and parameter-validation examples are incomplete. |
| `apps.evergo` | dashboard/tracking/customer/artifact endpoints | Partially documented (current state) | Auth and retry semantics are incomplete, including tokenized pages. |

### Other suite endpoint surfaces

| App | Endpoint surface | Status |
|---|---|---|
| `apps.core` | `core/urls.py` mounted routes | Not yet documented (current state) |
| `apps.links` | public redirects/reference routes | Not yet documented (current state) |
| `apps.docs` | docs reader/library routes | Not yet documented (current state) |
| `apps.features` | feature routes | Not yet documented (current state) |
| `apps.embeds` | embed routes | Not yet documented (current state) |
| `apps.teams` | team routes | Not yet documented (current state) |
| `apps.awg` | awg routes | Not yet documented (current state) |
| `apps.widgets` | widget routes | Not yet documented (current state) |
| `apps.certs` | certificate routes | Not yet documented (current state) |
| `apps.ops` | ops routes | Not yet documented (current state) |
| `apps.repos` | repo routes | Not yet documented (current state) |
| `apps.terms` | terms routes | Not yet documented (current state) |
| `apps.tasks` | task routes | Not yet documented (current state) |
| `apps.logbook` | logbook routes | Not yet documented (current state) |
| `apps.clocks` | clock routes | Not yet documented (current state) |
| `apps.meta` | meta routes | Not yet documented (current state) |
| `apps.shop` | shop routes | Not yet documented (current state) |
| `apps.sites` | non-webhook page/auth routes not listed above | Partially documented (current state) |

## Immediate maintenance actions
1. Update this matrix in every endpoint-related PR whenever endpoint contracts or auth behavior change.
2. Convert any "Partially documented (current state)" entry to "Documented (current state)" as soon as missing contract fields are added.
3. Convert any "Not yet documented (current state)" entry to "Partially documented (current state)" or "Documented (current state)" as soon as first-pass contract details are added.
