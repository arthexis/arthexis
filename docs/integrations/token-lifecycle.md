# Canonical API enrollment token lifecycle

Arthexis API consumers that use enrollment bearer tokens (Netmesh HTTP endpoints and node-network OCPP control actions) follow one canonical lifecycle.

## 1) Issuance path

- Tokens are minted through `apps.nodes.services.enrollment.issue_enrollment_token`.
- Admin reissue actions call the same service, so issuance and reissue share one code path.
- Preconditions:
  - target node record exists;
  - node state transitions to `PENDING` during issuance;
  - operator chooses an explicit `scope` (default: `mesh:read`).

## 2) Token format and required claims/scopes

- **Format**: opaque bearer token with `nmt1_` prefix.
- **Claims are server-side, not self-encoded**: `NodeEnrollment` stores canonical token claims:
  - enrollment id (lifecycle/version identity),
  - node id,
  - optional site id,
  - scope,
  - expiry,
  - status and revocation timestamps.
- Required scope per endpoint family:
  - `mesh:read` for Netmesh discovery/read APIs (`/api/netmesh/...`),
- `ocpp:control` for OCPP-adjacent control actions (`/nodes/network/chargers/action/`) when bearer auth is used.

## 3) Refresh/rotation strategy

- Reissue uses the same issuance service with `reissue=True`.
- Reissue revokes prior non-revoked, non-used, non-expired tokens before minting a new token.
- New token receives a fresh enrollment row (new lifecycle/version id).

## 4) Revocation and invalidation behavior

- Tokens are rejected when revoked, expired, inactive, or bound to nodes that are not enrolled.
- Revocation path updates node mesh enrollment state and records a `NodeEnrollmentEvent`.
- Authentication attempts update enrollment metadata (`last_authenticated_at`, `last_auth_error_code`) for support/incident review.

## 5) Failure modes and stable error codes

| HTTP status | Error code | Meaning |
| --- | --- | --- |
| 401 | `enrollment_token_missing` | No bearer token/header supplied. |
| 401 | `enrollment_token_invalid` | Token is malformed/unknown. |
| 401 | `enrollment_token_expired` | Enrollment token is expired. |
| 401 | `enrollment_token_revoked` | Enrollment token has been revoked. |
| 403 | `enrollment_scope_insufficient` | Token does not carry required scope for endpoint. |
| 403 | `enrollment_not_active` | Enrollment status is not active/public-key-submitted. |
| 403 | `node_not_enrolled` | Token node is not in enrolled mesh state. |
| 403 | `mesh_membership_missing` | Authenticated node has no enabled mesh membership in scope. |

Error payload shape is stable and machine-readable:

```json
{
  "error": {
    "code": "enrollment_token_invalid",
    "message": "invalid enrollment token"
  }
}
```
