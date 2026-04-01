# Documentation Completeness Checklist (HTTP + WebSocket Endpoints)

Use this page to track endpoint-documentation status across the Arthexis suite.

## Status rubric
- **Complete**: purpose, caller, request/response (including errors), auth/authorization, idempotency/retry, model/migration links documented.
- **In progress**: partial documentation exists but at least one of the required fields is missing.
- **Missing**: endpoint exists but no usable contract documentation yet.

## Charging and external integrations (prioritized)

| App | Endpoint group | Status | Notes |
|---|---|---|---|
| `apps.ocpp` | `/ocpp/chargers/`, `/ocpp/chargers/<cid>/`, `/ocpp/chargers/<cid>/action/` (plus connector-scoped action endpoint variant, e.g. `/ocpp/chargers/<cid>/connector/<connector>/action/`) | Complete | Primary charging control and state APIs documented in inventory page. |
| `apps.ocpp` | OCPP CSMS WebSocket catch-all (`/<...>/<cid>/`) | Complete | Auth + retry/idempotency behavior documented. |
| `apps.ocpp` | Firmware download + public status/log/chart/session pages | In progress | Needs per-endpoint error payload and auth matrix expansion. |
| `apps.nodes` | `/nodes/info/`, `/nodes/register/`, `/nodes/register/enrollment-public-key/` | Complete | Enrollment and registration contracts documented. |
| `apps.nodes` | `/nodes/network/chargers/`, `/nodes/network/chargers/forward/`, `/nodes/network/chargers/action/` | Complete | Signed peer sync and delegated charger control documented. |
| `apps.nodes` | `/nodes/net-message/`, `/nodes/net-message/pull/`, `/nodes/register/proxy/`, `/nodes/register/telemetry/` | In progress | Basic contract covered; add fuller error matrices and replay policies. |
| `apps.actions.api` | `/actions/api/v1/security-groups/` | Complete | Caller/auth and payload contract documented. |
| `apps.sites` | `/webhooks/whatsapp/` | Complete | Webhook request/response + feature gating documented. |
| `apps.sites` | `/login/passkey/options/`, `/login/passkey/verify/`, `/ws/pages/chat/` | In progress | Needs fuller role/scope/session-expiry detail and socket event schema. |
| `apps.cards` | RFID scan/import/export endpoints | In progress | Needs complete request/response examples for all variants. |
| `apps.video` | `/ws/video/...` sockets | In progress | Needs complete signaling/frame/error schema docs. |
| `apps.odoo` | `/odoo/query/<slug>/` | In progress | Needs explicit auth and parameter validation examples. |
| `apps.evergo` | dashboard/tracking/customer/artifact endpoints | In progress | Needs complete auth and retry semantics, especially tokenized pages. |

## Other suite endpoints (coverage backlog)

| App | Endpoint surface | Status |
|---|---|---|
| `apps.core` | `core/urls.py` mounted routes | Missing |
| `apps.links` | public redirects/reference routes | Missing |
| `apps.docs` | docs reader/library routes | Missing |
| `apps.features` | feature routes | Missing |
| `apps.embeds` | embed routes | Missing |
| `apps.teams` | team routes | Missing |
| `apps.awg` | awg routes | Missing |
| `apps.shortcuts` | shortcut routes | Missing |
| `apps.widgets` | widget routes | Missing |
| `apps.certs` | certificate routes | Missing |
| `apps.ops` | ops routes | Missing |
| `apps.repos` | repo routes | Missing |
| `apps.terms` | terms routes | Missing |
| `apps.tasks` | task routes | Missing |
| `apps.logbook` | logbook routes | Missing |
| `apps.clocks` | clock routes | Missing |
| `apps.meta` | meta routes | Missing |
| `apps.shop` | shop routes | Missing |
| `apps.sites` | non-webhook page/auth routes not listed above | In progress |

## Next pass recommendation
1. Finish `In progress` entries in charging- and integration-adjacent apps first (`ocpp`, `nodes`, `cards`, `sites`, `video`).
2. Move to remaining app URL surfaces by descending operational criticality.
3. Keep this checklist updated as part of each endpoint-related PR.
