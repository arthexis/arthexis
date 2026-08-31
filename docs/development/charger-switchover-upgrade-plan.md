# Charger Upgrade Switchover Plan

This plan defines a low-risk upgrade method that keeps chargers on the old instance until the new instance is proven ready, then switches traffic in a controlled cutover.

## Goals

- Keep charger sessions stable during upgrade preparation.
- Avoid forcing chargers to reconnect until cutover is approved.
- Provide clear rollback to the old instance.
- Fit Arthexis as the OCPP pivot by modeling switchover state in Django apps and admin.

## Non-Goals

- True active-active writes from both instances to the same runtime database.
- Eliminating all reconnects forever. OCPP chargers may still reconnect when endpoint authority changes.

## Architecture Summary

Use a **blue/green control-plane** pattern:

- **Blue**: current production instance currently serving chargers.
- **Green**: upgraded instance prepared in parallel.
- **Switchboard**: node-scoped routing and switchover state that controls when new charger connections move from blue to green.

### Key Rule

Existing charger sockets stay anchored to the instance where they connected. Only **new or reconnected** sockets are steered by switchboard policy.

## Data Model Additions

Create a small `cutover` app with admin-managed models:

1. `CutoverPlan`
   - name, state (`draft`, `preparing`, `ready`, `cutting_over`, `completed`, `rolled_back`)
   - blue_base_url, green_base_url
   - start_at, executed_at, rollback_deadline

2. `ChargerCutoverPolicy`
   - FK to `Charger`
   - target (`blue`, `green`, `inherit`)
   - drain_mode (`allow_existing`, `force_reconnect`, `block_new_on_blue`)
   - notes, updated_by

3. `ChargerCutoverLease`
   - charger identity
   - active_target (`blue` or `green`)
   - lease_started_at, last_seen_at
   - session_anchor (`connection_id` / node marker)

4. `CutoverEvent`
   - timestamped audit event stream for routing decisions, readiness checks, and rollback actions.

## Routing and Connection Flow

Integrate into `apps/ocpp/consumers/base/connection_flow.py` as a pre-admission decision:

1. Resolve charger identity.
2. Load `ChargerCutoverPolicy` (or inherit plan default).
3. If charger has an active lease on blue and drain mode is `allow_existing`, keep current socket on blue.
4. For new connections:
   - route to green when plan state is `ready` or `cutting_over` and policy allows.
   - otherwise route to blue.
5. Emit `CutoverEvent` with deterministic reason codes.

## Reset-Coordinated Final Cutover

When we are ready to retire blue, perform a coordinated charger reset + ingress switch
for chargers that are still connected to blue. This reduces failures where a charger
keeps a stale OCPP state machine context across instance reset.

1. Select connected blue chargers targeted for final cutover.
2. Pre-stage switchboard to route **new** connections for those chargers to green.
3. Send an OCPP Reset command to each selected charger (prefer `Soft`, allow `Hard`
   per charger policy or timeout fallback).
4. At the same cutover window, stop blue instance ingress for those charger identities.
5. Validate reconnect arrives on green and completes BootNotification/heartbeat flow.
6. Mark cutover lease moved to green and emit `CutoverEvent` reason code
   `reset_coordinated_cutover`.

This sequence ensures the charger restarts its own protocol state machine while routing
has already switched, so reconnection naturally lands on green.

## Upgrade Lifecycle

### Phase 1: Prepare Green

- Deploy upgraded green instance.
- Run migrations, fixtures, and feature toggles.
- Execute health checks and protocol checks on green.
- Keep charger ingress defaulting to blue.

### Phase 2: Readiness Gate

Gate cutover on explicit checks:

- `.venv/bin/python manage.py migrations check`
- `.venv/bin/python manage.py test run -- apps.ocpp`
- OCPP handshake smoke test against green websocket endpoint.
- Background worker and scheduler health (`celery` and critical tasks).

Mark `CutoverPlan.state = ready` only after gate success.

### Phase 3: Controlled Cutover

- Switch ingress policy for **new** charger connections to green.
- Keep existing blue sockets alive until natural reconnect, unless operator chooses forced reconnect by policy.
- Monitor transaction starts/stops, heartbeat continuity, and authorization error rates.
- For final retirement of blue, use reset-coordinated cutover for remaining blue-connected chargers.

### Phase 4: Drain and Complete

- Observe active blue lease count approaching zero.
- For stragglers, issue reset-coordinated cutover during a maintenance window.
- Mark plan `completed` when all targeted chargers are on green.

## Rollback

Rollback remains one switch:

1. Set plan state to `rolled_back`.
2. Route new connections back to blue.
3. Leave existing green sockets until reconnect or operator-forced recycle.
4. Record rollback event and reason.

Because chargers remain connection-anchored, rollback avoids immediate hard disconnect unless required.

## Admin UX

Add admin pages for:

- cutover plan dashboard with live counts (`blue active`, `green active`, `unassigned`)
- charger-level overrides and maintenance labels
- one-click actions:
  - `Mark ready`
  - `Start cutover`
  - `Rollback`
  - `Force reconnect selected chargers`

## Operational Commands

Add management command namespace:

- `.venv/bin/python manage.py cutover status`
- `.venv/bin/python manage.py cutover ready --plan <id>`
- `.venv/bin/python manage.py cutover start --plan <id>`
- `.venv/bin/python manage.py cutover rollback --plan <id> --reason "..."`
- `.venv/bin/python manage.py cutover drain --plan <id> --force-reconnect`
- `.venv/bin/python manage.py cutover reset-switchover --plan <id> --charger <id> [--hard]`

## Failure Modes and Safeguards

- **Split-brain routing**: prevent by keeping single authoritative cutover state in DB + cached read-through with short TTL.
- **Stale leases**: expire leases by heartbeat timeout and consumer disconnect hooks.
- **Premature cutover**: enforce readiness gate and explicit operator action.
- **Silent regressions**: require cutover event audit and alert thresholds on auth/transaction failures.
- **Reset command not acknowledged**: timeout and retry policy, then escalate to operator-approved hard reset.
- **Charger reconnects to blue after reset**: deny blue admission for charger identity once reset-switchover starts.

## Minimal Delivery Sequence

1. Introduce `cutover` app models + migrations + admin.
2. Add connection-flow routing hook + event logging.
3. Add `cutover` management commands.
4. Add reset-coordinated cutover command flow (reset dispatch + admission switch lock).
5. Add tests for routing, reset-switchover, and rollback semantics.
6. Add operator runbook under `docs/operations/`.

This sequence keeps implementation incremental while enabling immediate blue/green switchover control for chargers.
