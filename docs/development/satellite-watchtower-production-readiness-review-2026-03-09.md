# Satellite and Watchtower production readiness review (2026-03-09)

This review evaluates Arthexis production readiness for a deployment model where Satellite nodes are the highest-criticality systems, each running one charger-local server with SQLite, while Watchtower remains a lower-criticality informational and orchestration node. The main conclusion is that SQLite is acceptable under those assumptions; the key production concerns are Redis, restart behavior, process supervision, and how quickly runtime state recovers after a restart.

The repository already contains most of the pieces needed for this model, but the defaults are still more developer-friendly than appliance-strict. In particular, Satellite is close to viable for controlled production use now, while Watchtower is acceptable for lower-criticality deployment with clearer public-role configuration.

## Scope and assumptions

- One Satellite deployment per charger or charger group.
- SQLite is acceptable because write contention and concurrent admin usage are low.
- Satellite uptime is the highest priority, with downtime expected only during scheduled maintenance.
- Watchtower is second priority and may be unavailable without directly interrupting charging sessions.
- Findings are based on repository state as reviewed on 2026-03-09.

## Executive summary

### Satellite

- Conditionally ready for production under the stated deployment model.
- SQLite is not the limiting factor. The real operational dependencies are Redis-backed OCPP state, predictable systemd supervision, and explicit maintenance procedures.
- The largest remaining gap is not transaction persistence itself, but post-restart control-plane recovery: some operator actions still rely on in-memory active-session state rather than a persistent fallback.

### Watchtower

- Ready for lower-criticality informational deployment after explicit public-role configuration.
- It is more sensitive to Redis-backed Channels and public HTTPS proxy correctness than Satellite is.
- Its current downtime impact is primarily loss of visibility and orchestration, not direct interruption of charger-side energy delivery.

## Detailed findings

### 1. SQLite is acceptable for the target Satellite model

The current codebase already treats SQLite as a valid runtime path and applies WAL plus `busy_timeout` tuning on SQLite connections in `apps/core/apps.py`. For a one-server-per-charger appliance with minimal direct admin use, this is a reasonable choice. The main scaling and correctness pressure in this deployment model falls on Redis and on runtime state handling, not on database throughput.

This is reinforced by the role model itself:

- Satellite validation requires `OCPP_STATE_REDIS_URL`, not PostgreSQL.
- Celery defaults for non-Terminal roles already assume Redis.
- Installer presets require Redis for Satellite and Watchtower roles.

Relevant files:

- `apps/core/apps.py`
- `config/roles.py`
- `config/settings/channels.py`
- `config/settings/broker.py`
- `install.sh`

### 2. Satellite role wiring is broadly aligned with the target deployment

Satellite is described as "Multi-Device Edge, Network & Data Acquisition" in the role fixtures and README, which matches the intended edge-node use case. The installer preset sets `NODE_ROLE=Satellite`, enables Celery, assigns a service name, and requires Redis.

Important nuance: the installer default is still `embedded` service-management mode unless `--systemd` is explicitly passed. That means the deployment gets a systemd-managed main service, but Celery worker and beat are usually spawned by `scripts/service-start.sh` as child processes instead of receiving their own independent systemd units.

This is workable, but it is not the cleanest production shape for the highest-criticality role.

Relevant files:

- `README.md`
- `apps/nodes/fixtures/node_roles__noderole_satellite.json`
- `install.sh`
- `scripts/helpers/systemd_locks.sh`
- `scripts/service-start.sh`

### 3. OCPP continuity is stronger than UI/control continuity

The OCPP implementation has a useful split:

- Pending CSMS calls are persisted to Redis with TTL and restored on reconnect.
- Transaction rows are persisted in the database.
- Some dashboard and API paths can recover active-session visibility from the database when in-memory cache is empty.

This is a strong foundation for Satellite appliances. It means a restart does not automatically imply loss of all charging context.

However, active websocket connections and active transactions are still primarily held in process memory in `apps/ocpp/store/state.py`. That becomes important after a service restart:

- Several operator and control paths still rely only on `store.get_transaction(...)`.
- Some dashboard paths are more resilient because they fall back to the database or latest transaction rows.

Practical effect:

- The charging session itself may continue correctly.
- A recently restarted Satellite can temporarily lose accurate "active session" awareness in the control plane until new OCPP traffic refreshes the in-memory store.
- During that window, remote-stop and reset guardrails are softer than they should be.

Relevant files:

- `apps/ocpp/store/state.py`
- `apps/ocpp/store/pending_calls.py`
- `apps/ocpp/consumers/base/connection_flow.py`
- `apps/ocpp/views/common.py`
- `apps/ocpp/views/dashboard.py`
- `apps/ocpp/views/actions/core.py`
- `apps/nodes/views/ocpp.py`
- `apps/ocpp/admin/charge_point/actions/remote_control.py`

### 4. Startup intentionally clears cached charger status fields

On startup, the app clears persisted charger status fields such as `last_status`, `last_error_code`, and `last_status_timestamp`. This is a defensible choice because it avoids stale "charging", "available", or "faulted" displays after crashes or unclean shutdowns.

The tradeoff is observability lag:

- Immediately after restart, dashboards and APIs may show blank or degraded status.
- Accurate status is restored only after fresh heartbeats or `StatusNotification` traffic arrives.

For Satellite, this is mostly an operator-visibility issue rather than a charger-delivery issue. For Watchtower, it reinforces the role's informational nature.

Relevant files:

- `apps/ocpp/apps.py`
- `apps/ocpp/status_resets.py`

### 5. Startup and upgrade flows are serviceable but still need production bundling

The runtime scripts are better than an ad hoc Django deployment:

- Managed service units use `Restart=always`.
- Startup records timing and state.
- Predeploy migration orchestration exists.
- Upgrade logic remembers whether the service was running and attempts to recover after failures unless `--no-start` was requested.

There are still two important operational caveats for Satellite:

1. `runserver_preflight` can auto-apply migrations on startup.
2. The installer defaults do not push production roles onto the stricter systemd split by default.

For always-on Satellites, implicit startup migration should be treated as fallback behavior, not the normal deployment plan.

Relevant files:

- `scripts/helpers/runserver_preflight.sh`
- `scripts/helpers/predeploy-migrate-orchestrator.sh`
- `scripts/helpers/systemd_locks.sh`
- `scripts/service-start.sh`
- `upgrade.sh`

### 6. Satellite is safer in upgrades than first impressions suggest

`upgrade.sh` does not treat Satellite as one of the non-terminal roles that automatically discard dirty working tree changes. That auto-discard behavior applies to `Control`, `Watchtower`, and `Constellation`, and also to explicit auto-upgrade or force flows.

This matters because it makes Satellite more compatible with cautious, scheduled maintenance. The production recommendation is still to keep Satellite on fixed/manual upgrades unless there is a strong operational reason to do otherwise.

Relevant files:

- `upgrade.sh`
- `docs/development/install-lifecycle-scripts-manual.md`

### 7. Watchtower is aligned with the user's stated lower-criticality posture

Watchtower is modeled as the cloud and orchestration role. It requires a Redis-backed Channels backend, and readiness rules include Watchtower-specific orchestration concerns such as AWS credential presence.

The impact of Watchtower downtime is mostly:

- loss of aggregate visibility,
- delayed orchestration,
- degraded network-level operator workflows.

That is materially different from Satellite, where restart behavior is closer to the charger session itself.

Relevant files:

- `README.md`
- `config/roles.py`
- `config/channel_layer.py`
- `apps/counters/dashboard_rules.py`
- `apps/nodes/fixtures/node_roles__noderole_watchtower.json`

## Recommended code improvements

The most valuable code work is not generic refactoring. It is targeted reliability work that removes operator-state gaps and makes role presets reflect actual production intent.

### General improvements

1. Make production role service mode explicit.

Add a role-aware default so production roles can opt into `systemd` mode without requiring operators to know that `embedded` is the current default. At minimum, make the role preset output clearly state which service-management mode is being installed and why.

2. Add a startup migration policy setting.

Introduce a setting or env var such as `ARTHEXIS_MIGRATION_POLICY=apply|check|skip`.

Recommended default behavior:

- Satellite: `check`
- Watchtower: `apply` or `check`, depending on deployment model
- Terminal: `apply`

This would make startup behavior more predictable and maintenance windows easier to control.

3. Close the post-restart active-session blind spot.

The control plane should not rely solely on in-memory active transaction cache for decisions like:

- remote stop eligibility,
- reset safety,
- local admin state display.

Recommended fix:

- add a shared helper that resolves active session from memory first, then persistent transaction state,
- reuse it across admin actions, API actions, and node-to-node control flows.

4. Add an explicit charger state resync path after reconnect or startup.

After a reconnect, trigger a bounded status resync flow so the control plane converges faster. This could use `TriggerMessage(StatusNotification)` or a similar OCPP-side refresh mechanism where supported.

5. Promote Redis diagnostics into health checks.

The repository already has `manage.py redis`, but Redis health is important enough to appear in `manage.py health` with role-aware targets such as:

- `core.redis`
- `ocpp.redis_state`
- `channels.redis`
- `celery.broker`

This would make preflight and ongoing operational checks much more direct.

6. Add a public-role HTTPS security bundle.

For public deployments, especially Watchtower, add explicit configuration for secure cookies and HSTS rather than relying only on proxy-header handling. Public-role defaults should make the safe posture obvious and easy to enable.

### Satellite-specific improvements

1. Default Satellite to split systemd supervision.

For a charger appliance, Satellite should likely install with separate Django, Celery worker, and Celery beat units by default.

2. Write explicit OCPP Redis settings during install.

Even though settings currently resolve `OCPP_STATE_REDIS_URL` from the broker when absent, the installer should write the value explicitly. This makes the deployment clearer and makes it easier to split Redis logical databases later.

3. Add a Satellite-safe control mode after restart.

Until active session state is revalidated, the UI should prefer caution:

- allow read-only visibility,
- delay reset actions,
- resolve remote-stop against persisted state where possible.

### Watchtower-specific improvements

1. Add Watchtower-focused health and readiness checks.

Explicitly validate:

- Redis-backed Channels,
- upstream propagation health,
- AWS credentials when required,
- public HTTPS proxy expectations.

2. Bundle public-role security defaults.

Watchtower is the role most likely to sit behind a public hostname and CDN or reverse proxy. Its role preset should bundle the expected HTTPS/security posture more explicitly than it does now.

## Recommended configuration bundles

The repository should bundle both global and role-specific deployment configuration instead of relying on implicit settings fallbacks.

### Global bundle

Recommended for all non-development installs:

- generated role env file with explicit `NODE_ROLE`,
- generated Redis env file with masked diagnostics support,
- explicit service-management mode,
- role-appropriate upgrade policy,
- preconfigured health-check commands for post-install verification.

### Repository workflow guardrails

Recommended for all production-oriented work:

- disable direct pushes to `main`,
- require pull requests for changes that affect production roles or deployment scripts,
- require CI and at least one review before merge,
- keep release automation compatible with branch protection rather than exempting it.

This is especially important here because a large share of the production risk
is concentrated in installer scripts, settings defaults, and role-specific runtime
behavior. Those are exactly the kinds of changes that benefit from PR-only
review and protected-branch enforcement.

### Satellite bundle

Recommended defaults:

Role bundles should materialize concrete env vars (listed below):

- `NODE_ROLE=Satellite`
- `OCPP_STATE_REDIS_URL`
- `CELERY_BROKER_URL`
- `CELERY_RESULT_BACKEND`
- split `systemd` supervision by default
- fixed/manual upgrades by default
- startup migration policy set to `check`

Nice-to-have additions:

- separate Redis logical DB or endpoint for OCPP state vs Celery,
- role-specific post-restart "state resync pending" indicator in UI,
- a one-command post-install verification script that checks Redis, service health, OCPP state backend, and role validation.

### Watchtower bundle

Recommended defaults:

Role bundles should materialize concrete env vars (listed below):

- `NODE_ROLE=Watchtower`
- `CHANNEL_REDIS_URL`
- `CELERY_BROKER_URL`
- public HTTPS proxy expectations documented and bundled
- split `systemd` supervision by default
- public-role cookie and HSTS settings enabled when HTTPS is expected

Nice-to-have additions:

- Watchtower-specific health-check group,
- explicit AWS readiness summary when that integration is enabled,
- simpler deployment checklist for reverse proxy, host validation, and Channels transport.

## Recommended implementation order

### P0

1. Rework control-path active-session resolution to use persistent fallback, not only in-memory state.
2. Add an explicit startup migration policy and default Satellite to non-applying mode.
3. Make Satellite default to split systemd supervision.

### P1

1. Write explicit role-specific Redis env variables during install.
2. Add Redis and Channels health targets to `manage.py health`.
3. Add reconnect or restart state resync for charger status visibility.

### P2

1. Add public-role HTTPS security bundle for Watchtower.
2. Add role-specific post-install verification commands and documentation shortcuts.
3. Reduce implicit settings fallbacks where production behavior is currently "smart" but not obvious.

## Final recommendation

Yes: both code improvements and bundled configuration changes are worth doing.

The highest-value split is:

- bundle more production behavior into role presets,
- keep SQLite for the stated Satellite model,
- treat Redis as the actual critical shared dependency,
- make Satellite stricter by default than Watchtower,
- close the active-session recovery gap so restart behavior matches appliance expectations.

If only a few improvements are funded first, prioritize Satellite-safe restart behavior and explicit production role presets over broader refactoring.
