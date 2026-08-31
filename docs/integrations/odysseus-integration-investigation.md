# Odysseus Integration Investigation

Reviewed on 2026-06-07 against Odysseus upstream `dev` commit
`108ee1e32b2d0022a94d0ff2396b137dc3d2ece9`.

## Summary

Odysseus by PewDiePie is a self-hosted AI workspace with chat, agents,
documents, memory, email, calendar, research, model serving, MCP, and Codex
integration surfaces. A guarded Arthexis integration is viable, but the first
implementation should be an optional scoped HTTP connector rather than an
embedded runtime or container-managed service.

Follow-up implementation issue: #8588, "Add optional Odysseus scoped API
connector".

## Upstream Sources

- Project: `https://github.com/pewdiepie-archdaemon/odysseus`
- Homepage: `https://pewdiepie-archdaemon.github.io/odysseus/`
- License: MIT (`LICENSE`)
- Reviewed files:
  - `README.md`
  - `SECURITY.md`
  - `THREAT_MODEL.md`
  - `routes/api_token_routes.py`
  - `routes/webhook_routes.py`
  - `routes/mcp_routes.py`
  - `routes/codex_routes.py`
  - `integrations/codex/README.md`
  - `integrations/codex/.codex-plugin/plugin.json`
  - `integrations/codex/skills/odysseus/SKILL.md`
  - `integrations/codex/scripts/odysseus_api.py`

## Supported Integration Paths

### 1) Scoped Codex HTTP API - recommended

Odysseus ships a Codex plugin/skill bundle that directs external Codex sessions
through scoped HTTP endpoints under `/api/codex/*`. The bundle explicitly says
Codex must use those endpoints and must not use SSH, direct Python
imports, database queries, or MCP internals to access user data.

Relevant upstream endpoints include:

| Endpoint | Purpose | Arthexis suitability |
| --- | --- | --- |
| `GET /api/codex/capabilities` | Discover token scopes and enabled tools | Use as the first health/capability check. |
| `GET/POST /api/codex/todos` | Read and update todos/reminders | Good first low-risk workflow after capability checks. |
| `GET/POST/DELETE /api/codex/memory` | Manage scoped memory | Useful later, but needs clear ownership language. |
| `GET/POST/DELETE /api/codex/calendar/events` | Calendar integration | Useful later; must preserve user confirmation expectations. |
| `GET/POST/DELETE /api/codex/documents` | Document library integration | Useful later for operator notes or handoff docs. |
| `GET /api/codex/emails` (and draft/send endpoints) | Email integration | Read/draft only by default; send requires explicit user action. |
| `GET/POST /api/codex/cookbook/*` | Model serving task visibility/control | Defer. This can launch long-running host processes. |

Arthexis should start by calling `GET /api/codex/capabilities`, fail closed on
missing scopes or `403`, and expose only the workflows the Odysseus token
advertises.

### 2) Token-managed chat endpoint - possible, not first

`POST /api/v1/chat` accepts Odysseus API-token authenticated chat requests with
the `chat` scope. It can resume owned sessions, create sessions from a direct
provider API key, or use configured model endpoints. This is a viable future
surface for Arthexis-originated prompts, but it is less attractive as the first
connector because it may spend provider/model quota and create user-visible
conversation state.

### 3) Webhooks - possible event ingress

Odysseus has admin-managed webhook registration under `/api/webhooks`. Its chat
route fires a `chat.completed` event. Arthexis can consider inbound event
handling later, but the first connector does not need webhook registration.

### 4) MCP server management - not an Arthexis integration shortcut

Odysseus can manage MCP servers through admin-only `/api/mcp/*` routes. That is
powerful host-level configuration, especially for stdio servers. Arthexis should
not use those routes to bypass the scoped Codex API. If Arthexis later exposes
an MCP server for Odysseus to consume, that should be a separate issue with its
own auth, audit, and operator controls.

## Constraints

- External container runtimes must not be required for Arthexis, Arthexis
  development, or this connector. Raspberry Pi-class nodes use native suite
  paths, and the Arthexis connector only needs HTTP.
- Use Odysseus only through supported scoped HTTP endpoints, preferably
  `/api/codex/*`.
- Do not scrape the Odysseus UI, read its local files, query its database, reuse
  browser cookies, call Python internals, depend on container networking, or
  reach through MCP internals for user data.
- Treat Odysseus as a private admin-console-like service. Upstream guidance says
  to keep auth enabled, protect tokens and data directories, avoid public
  unauthenticated exposure, and use HTTPS, VPN, Tailscale, or a trusted reverse
  proxy beyond loopback.
- Keep model serving/Cookbook control out of the first implementation. Cookbook
  actions can start or stop long-lived host processes and should require a
  later explicit opt-in.

## Legal And Licensing

Odysseus is MIT licensed, so an optional Arthexis connector can interoperate
with it and may reference small setup snippets or protocol descriptions with
attribution. Do not vendor Odysseus code unless the MIT notice is preserved.

Odysseus optional dependencies can include packages with stronger license terms
for specific features. The Arthexis connector should avoid depending on those
optional runtime packages and communicate only over HTTP.

## Recommended Arthexis Boundary

Implement #8588 as an optional connector:

1. Store an Odysseus base URL and scoped API token through existing Arthexis
   secret/config patterns.
2. Add a small HTTP client with short timeouts, JSON validation, explicit error
   mapping, and no automatic retry for writes.
3. Start with `GET /api/codex/capabilities` and a read-only admin/status view.
4. Add one low-risk workflow after capability discovery is green, preferably
   todos or documents.
5. Require explicit enablement for any write path.
6. Require a separate issue for email send, Cookbook/model serving, shell/MCP,
   or privileged automation.

## Open Questions

- Odysseus is moving quickly and has no formal release tag yet. The connector
  should either document a tested upstream commit range or treat endpoint drift
  as an integration health failure.
- `routes/codex_routes.py` includes Cookbook scopes such as `cookbook:read` and
  `cookbook:launch`, while the reviewed token-management route listed token
  scopes for chat, todos, documents, email, calendar, and memory. Do not depend
  on Cookbook token grants until upstream token issuance is verified live.
- Arthexis needs a clear operator UX for "local/private Odysseus URL" versus
  "remote Odysseus behind Tailscale/VPN/reverse proxy" so users do not expose an
  admin console directly to the public internet.

## Verification

This document is documentation-only. The investigation was verified by reviewing
official upstream repository files and creating follow-up implementation issue
#8588 for the viable scoped HTTP connector path.
