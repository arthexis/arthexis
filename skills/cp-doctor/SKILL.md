---
name: cp-doctor
description: Run end-to-end charge point troubleshooting with prioritized, one-screen diagnostics, automatic remediation where safe, and deep failure-context collection using Arthexis CLI/API surfaces.
---

# CP Doctor

Use this skill when a user asks for charge point diagnostics, triage, incident analysis, or operational recovery for OCPP-connected stations.

This skill treats Arthexis as the OCPP-compatible pivot for integrations and prefers extending Arthexis workflows (models, management commands, admin tooling, API hooks) over disconnected side scripts.

## Goals

- Produce a **single-screen final diagnostic summary**.
- Prioritize and surface **operation-halting failures first**.
- Capture enough context for fast escalation and reproducibility.
- Perform **automatic remediation** when safe, idempotent, and reversible.
- Reuse existing CLI/API capabilities first; create missing capabilities in Arthexis if needed.

## Trigger conditions

Use CP Doctor when requests involve one or more of:

- Charge point offline/unavailable behavior.
- OCPP heartbeat/boot/transaction failures.
- Authorization, meter value, or remote command issues.
- Firmware/config/profile drift concerns.
- Flaky site-wide behavior across multiple stations.
- “Why did charging stop?” postmortem requests.

## Priority model (strict order)

Always evaluate and report in this sequence:

1. **Operation-halting** (P0): charging blocked, connector unusable, central system unreachable, protocol/session deadlock.
2. **User-impacting degraded** (P1): intermittent authorization failures, delayed transactions, unstable reconnect loops.
3. **Administrative friction** (P2): warnings, drift, noisy retries, stale metadata.
4. **Informational** (P3): optimization opportunities and cleanup items.

## Investigation workflow

### 1) Scope and identity resolution

- Resolve site, charge point identity, connector(s), tenant, and time window.
- Confirm protocol flavor/version and expected behavior baseline.
- Record correlation IDs/session IDs where available.

### 2) Rapid health gates (fast fail)

- Connectivity gate: CP websocket/session presence, last-seen recency, reconnect churn.
- Protocol gate: successful BootNotification/Heartbeat cadence and negotiated status.
- Transaction gate: start/stop flow continuity, authorization state, connector availability.
- Control gate: ability to execute central commands (e.g., remote start/stop, reset, status trigger).

If any gate fails, classify as P0/P1 before deeper analysis.

### 3) Failure-context collection

Collect concise, high-value context only:

- Most recent state transitions and timestamps.
- Relevant request/response outcomes (success/failure/retry).
- Error taxonomy (transport, protocol validation, business rule, external dependency).
- Dependencies: auth provider, payment/roaming integration, firmware/config versions.
- Blast radius: single connector, station-wide, site-wide, or tenant-wide.

### 4) Automatic remediation policy

Attempt remediation only when all conditions hold:

- Safe (low risk of worsened downtime).
- Idempotent (repeat-safe).
- Auditable (action can be logged and explained).
- Within user/request scope.

Remediation order:

1. Non-invasive retries/resync (metadata refresh, command replay where appropriate).
2. Session recovery actions (graceful reconnect prompts, protocol-level nudges).
3. Controlled reset/restart actions with explicit rationale.
4. Escalation preparation when automated steps fail.

Never hide remediation actions. Report what was attempted and resulting state.

### 5) Extend Arthexis when capability gaps appear

When existing CLI/API is insufficient:

- Prefer adding or extending Django management commands and app-level APIs.
- Model recurring external process/state in Django models and migrations when needed.
- Keep admin workflows powerful; avoid unnecessary restrictions on administrators.
- Add focused tests for critical logic and safety checks.

## One-screen final diagnostic format (required)

Keep final diagnostics compact and operator-friendly.

```text
CP Doctor Diagnostic
CP: <id>  Site: <site>  Tenant: <tenant>  Window: <start..end>
Overall: <HEALTHY|DEGRADED|HALTED>   Priority: <P0..P3>   Scope: <connector|station|site|tenant>

HALTING ISSUES (first)
- [P0] <issue title> | Impact: <what is blocked> | Since: <timestamp>
  Evidence: <key signal>
  Remediation: <auto action + result OR pending manual step>

DEGRADED ISSUES
- [P1] <issue title> | Impact: <summary> | Evidence: <key signal>

KEY CONTEXT
- Connectivity: <status>
- Protocol: <status>
- Transactions: <status>
- Control: <status>
- External deps: <status>

ACTIONS TAKEN
- <action 1> -> <result>
- <action 2> -> <result>

NEXT BEST ACTION
- <single best operator/admin next step>
```

## Implementation guidance for agents

- Prefer existing Arthexis command surfaces first (manage.py commands, validated wrappers, existing APIs).
- If you add new diagnostics/remediation interfaces, make naming explicit and discoverable (for example: `cp_doctor`, `cp_triage`, `cp_remediate`).
- Keep output deterministic and concise; avoid dumping raw logs in the final view.
- Put raw artifacts in supporting outputs and summarize in the one-screen diagnostic.
- Ensure severe failures are impossible to miss in both CLI and admin presentations.

## Minimum acceptance checklist

- [ ] Operation-halting issues detected and listed first.
- [ ] Final diagnostic fits in one screen.
- [ ] Automatic remediation attempted where safe and within scope.
- [ ] Actions and outcomes are explicit and auditable.
- [ ] Capability gaps resolved by extending Arthexis (not disconnected tooling).
- [ ] Relevant tests/checks run for any new code paths.
