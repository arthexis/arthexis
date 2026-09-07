# Core boundary follow-up plan

This document tracks the remaining work intentionally split out of PR #87.

No implementation should begin until Install Health is green on `main` after PR #87.

## Scope

### Step 8 — Complete health/operator service extraction

- Inventory remaining domain-owned health and operator implementations still under `apps.core`.
- Move each implementation to its canonical owning app.
- Preserve narrow compatibility imports, command surfaces, task names, and runtime behavior where required.
- Keep `apps.ops` universally installed during this transition.
- Add focused ownership and compatibility tests for each moved service.

### Step 9 — Slim `CoreConfig.ready()`

- Audit startup wiring currently performed by `apps.core`.
- Move domain-specific startup registration to owner apps.
- Keep only domain-independent runtime/bootstrap behavior in core.
- Add startup-order and profile coverage where moving hooks could affect initialization.

### Step 10 — Final boundary enforcement and compatibility cleanup

- Tighten import-linter contracts to reflect the new ownership graph.
- Remove compatibility layers only when their transition requirements are satisfied.
- Keep shrink-only enforcement for the remaining `apps.core` surface.

## Compatibility hardening

- Add persisted `django_celery_beat.PeriodicTask` compatibility coverage for historical maintenance/release task names and normalization.
- Retain and test historical queued Celery task aliases for at least one transition window.
- Audit persisted/serialized Django model labels formerly under `core`, including `AdminNotice`, `UsageEvent`, and `InviteLead`.
- Audit project-specific GenericForeignKey and serialized ContentType consumers.
- Keep the universal `apps.ops` invariant unless a separate reviewed change explicitly removes it with profile/node behavior tests.

## Suggested implementation order

1. Confirm Install Health is green on `main` after PR #87.
2. Inventory remaining Step 8 symbols and dependencies.
3. Complete Step 8 in small owner-focused commits with focused tests.
4. Run compatibility regressions and broaden CI coverage as needed.
5. Complete Step 9.
6. Complete Step 10 and compatibility cleanup.
7. Run full required CI and manual boundary regressions before merge.

## Non-goals for the initial draft

- No service moves.
- No startup behavior changes.
- No compatibility-layer removals.
- No import-linter tightening beyond documenting the intended end state.
