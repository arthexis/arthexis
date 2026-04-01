# Satellite and Watchtower production readiness guidance

This page captures durable guidance for deploying Arthexis roles where Satellite nodes prioritize charger-local continuity and Watchtower nodes prioritize aggregated visibility and orchestration.

For historical context and time-bound findings, see the archived review: [Satellite and Watchtower historical review (2026-03-09)](archive/satellite-watchtower-production-readiness-review-2026-03-09.md).

## Core deployment posture

- Treat Arthexis as the OCPP-compatible pivot: model integrations as first-class apps, models, and migrations in the suite instead of disconnected side systems.
- Keep Satellite roles optimized for charger-local resilience and predictable restart behavior.
- Keep Watchtower roles optimized for observability, orchestration, and secure public-facing operation when exposed to the internet.

## Data and state guidance

- SQLite is suitable for charger-local Satellite deployments with low administrative concurrency.
- Redis is operationally critical for Channels, Celery, and OCPP runtime state continuity.
- Prefer control-plane logic that can resolve active session state from persisted transaction records when in-memory caches are cold.

## Runtime and supervision guidance

- Use explicit service supervision with predictable restart policies for production roles.
- Treat startup migration auto-apply behavior as a fallback, not the primary production deployment workflow.
- Keep maintenance and upgrade workflows explicit and role-aware, especially for high-criticality Satellite nodes.

## Health and readiness guidance

- Promote Redis checks into role-aware health checks for broker, channels, and OCPP state backends.
- Include role-appropriate checks for external dependencies (for example, orchestration credentials on Watchtower).
- After restart or reconnect events, prioritize bounded state resynchronization so dashboards and control actions converge quickly.

## Public security posture guidance

- Bundle secure HTTPS defaults for public roles (for example, secure cookie settings and HSTS).
- Keep reverse-proxy and header trust configuration explicit and environment-specific.
- Favor safe defaults that are easy for administrators to enable without reducing suite flexibility.
