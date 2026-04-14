# Arthexis onboarding tracks (operator + integrator)

This page is a concise, living onboarding track for the two most common Arthexis starts:

1. **Operator path** (admin setup, monitoring, common actions)
2. **Integrator path** (authentication, endpoint usage, webhook/OCPP integration sequence)

Arthexis should be treated as an OCPP-compatible WebSocket server and integration pivot. Extend workflows through suite apps, Django models, migrations, and admin tools instead of side systems.

## Prerequisites and environment assumptions

- You have a running Arthexis environment with dependencies installed.
- You can authenticate to Django admin with a user that has permission for OCPP and Nodes objects.
- Charger/network endpoints are reachable from your deployment network.
- Time sync and TLS basics are already in place (for token expiry and secure endpoint usage).
- You can access key integration docs:
  - [Endpoint inventory](endpoint-inventory.md)
  - [Token lifecycle and stable auth errors](token-lifecycle.md)
  - [OCPP user manual](../development/ocpp-user-manual.md)

---

## Path 1: Operator onboarding track

### Step 1 — admin baseline setup

1. Log in to `/admin/`.
2. Confirm fleet and enrollment records exist and are current:
   - **Nodes** (`Node`, `NodeEnrollment`, `NodeEnrollmentEvent`)
   - **OCPP** (`Charger`, `Transaction`)
3. Verify at least one enrolled node has valid enrollment/token state before attempting remote control flows.

### Step 2 — monitoring baseline

1. Open charger dashboards and charger detail pages from OCPP views.
2. Check recent transaction and event flow in admin:
   - `Transaction` rows for active/completed sessions
   - enrollment/auth failures on `NodeEnrollment.last_auth_error_code`
3. Confirm service health visibility through the Suite Services report when operating a node environment.

### Step 3 — common operator actions

1. Use charger action endpoints/UI flows to run one safe command (for example a status-oriented or connector-scoped action).
2. Validate action result in logs/admin history before retrying.
3. For token-related auth failures, rotate/reissue through the enrollment flow and retry once.

### First successful operator happy path

A successful first run is:

1. Admin login works.
2. You can see one charger in OCPP list/detail views.
3. You send one control action and observe the expected outcome trail (request + response/log).
4. You can identify where auth errors would appear if enrollment tokens become invalid.

---

## Path 2: Integrator onboarding track

### Step 1 — authentication model and scope selection

1. Choose the endpoint family you are integrating first:
   - Netmesh peer task policy discovery (`mesh:read`)
   - node-network OCPP control (`ocpp:control`)
2. Mint/reissue enrollment token through the canonical enrollment service/admin flow.
3. Store token as a secret; do not embed it in source or scripts committed to git.

### Step 2 — endpoint usage sequence

1. Start with one read endpoint from inventory and confirm `200` behavior.
2. Move to one write/control endpoint and verify idempotency/retry expectations.
3. Capture response envelope shape and error handling against stable error codes.

### Step 3 — webhook/OCPP integration sequence

Use this sequence for first integration success:

1. **Authenticate** with an enrollment token carrying required scope.
2. **Call HTTP control endpoint** (or relevant integration endpoint) with minimal valid payload.
3. **Observe OCPP-side effect** in charger/session/admin logs.
4. **Correlate response + event trail** to confirm full handshake.
5. **Add retry policy** only after baseline success (token/auth failures handled separately from transport retries).

### First successful integrator happy path

A successful first run is:

1. Token accepted for selected scope.
2. One endpoint call returns success and expected payload shape.
3. Corresponding charger/event state is visible in Arthexis models/admin.
4. Integrator can distinguish auth failures from business/validation failures.

---

## Troubleshooting map (by common status + code/message)

Use the token lifecycle page as canonical semantics source. Start here for triage:

| Status | Code/message | Meaning | Immediate action |
| --- | --- | --- | --- |
| 401 | `enrollment_token_missing` | No bearer token provided | Add `Authorization: Bearer <token>` and retry. |
| 401 | `enrollment_token_invalid` / `invalid enrollment token` | Malformed/unknown token | Recopy token, ensure no truncation/whitespace, retry once. |
| 401 | `enrollment_token_expired` | Token expired | Reissue token, redeploy secret, retry. |
| 401 | `enrollment_token_revoked` | Token revoked | Mint fresh token, disable stale automations using old token. |
| 403 | `enrollment_scope_insufficient` | Scope does not match endpoint family | Reissue token with required scope (`mesh:read` or `ocpp:control`). |
| 403 | `enrollment_not_active` | Enrollment state not active/public-key-submitted | Complete enrollment state transition before API usage. |
| 403 | `node_not_enrolled` | Node not in enrolled mesh state | Fix node enrollment state and re-authenticate. |
| 403 | `mesh_membership_missing` | No enabled mesh membership in requested scope | Enable/repair membership mapping and retry. |

If status/code does not match this table, check endpoint-specific contracts in the inventory and OCPP manual, then inspect model/admin history for correlated events.

---

## Where to extend workflows in Arthexis (models + admin)

Prefer extending Arthexis directly through apps/models/migrations/admin when you need new behaviors.

### Core extension anchors

- **Enrollment/auth workflow models**:
  - `apps.nodes.models.enrollment.NodeEnrollment`
  - `apps.nodes.models.enrollment.NodeEnrollmentEvent`
- **Charging lifecycle models**:
  - `apps.ocpp.models.charger.Charger`
  - `apps.ocpp.models.transaction.Transaction`
- **Endpoint/action integration surfaces**:
  - `apps.actions.api` endpoints
  - `apps.netmesh.api` peer task policy endpoints

### Admin entry points for operations and extension

- Node/admin enrollment tooling: `apps.nodes.admin.enrollment_admin`
- Node operations: `apps.nodes.admin.node_admin`
- OCPP transactional and charger admin surfaces:
  - `apps.ocpp.admin.miscellaneous.transactions_admin`
  - `apps.ocpp.admin.miscellaneous.core_admin`

When adding new integration behavior, model it first (Django model + migration), expose admin discoverability, then wire endpoint/consumer logic.

---

## Ownership and update triggers (living doc contract)

- **Primary owner**: maintainers touching `apps.nodes`, `apps.netmesh`, `apps.actions`, or `apps.ocpp` auth/control paths.
- **Secondary owner**: docs maintainer for integration docs consistency.

Update this page in the same PR whenever one of these changes occurs:

1. Enrollment/auth scope semantics or stable auth error codes change.
2. Endpoint contract behavior changes (request fields, response envelope, status mapping, idempotency, retry semantics).
3. OCPP control flow sequence changes (control request, async response, persistence trail).
4. Admin model locations/names used in onboarding flows are moved/renamed.

Recommended PR checklist item: **"Onboarding tracks reviewed/updated for auth and endpoint behavior changes."**
