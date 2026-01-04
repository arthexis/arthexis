# MCP domain

The MCP domain provisions Model Context Protocol servers inside an Arthexis instance. Each server is tied to a specific acting user and exposes unique URLs, endpoints, and shared secrets for agentic clients.

## Core model
* `MCPServer` tracks the human-friendly name, unique slug, acting user, enablement flag, and per-server API secret. Helpers on the model generate fully-qualified endpoint URLs for use in manifests and clients.【F:apps/mcp/models.py†L18-L78】

## HTTP endpoints
* **Manifest** – `GET /mcp/api/servers/<slug>/manifest/` requires the `X-MCP-Secret` header or `?secret=` parameter and returns the server descriptor, acting user, and derived endpoints. Secrets can be rotated via `POST /mcp/api/servers/<slug>/rotate-secret/` by a staff user.【F:apps/mcp/api/views.py†L10-L43】
* **RPC Gateway** – `POST /mcp/<slug>/rpc/` validates the same MCP secret and reports the ready state for downstream agent wiring.【F:apps/mcp/views.py†L10-L25】
* **Event sink** – `POST /mcp/<slug>/events/` accepts agent event submissions while enforcing the server secret.【F:apps/mcp/views.py†L28-L42】
* **Health** – `GET /mcp/<slug>/health/` is available when the server is enabled to support liveness checks.【F:apps/mcp/views.py†L45-L49】

## Admin surface
`MCPServer` entries appear in Django admin for staff to assign the acting user, toggle availability, and view the generated secret. Rotation is also available through the manifest API for automation workflows.【F:apps/mcp/admin.py†L1-L17】【F:apps/mcp/api/views.py†L26-L34】
