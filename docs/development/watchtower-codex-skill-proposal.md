# Proposed Codex skill: `watchtower-attack-drill`

This proposal defines a reusable Codex skill that operationalizes the Watchtower attack simulation playbook into a repeatable, evidence-driven workflow.

## Why this skill

Operators need a fast, standardized way to:

- run basic internet-facing attack simulations safely,
- collect pass/fail evidence, and
- produce a hardening plan when controls fail.

The skill reduces manual drift by giving Codex deterministic scripts for repeatable checks while keeping policy and interpretation in references.

## Trigger guidance

The skill should trigger when users ask to:

- simulate attacks against Watchtower nodes,
- run resilience drills for internet exposure,
- validate basic protections (rate limiting, lockouts, timeout behavior, TLS posture),
- generate a security-drill report with remediation actions.

## Skill package layout

```text
watchtower-attack-drill/
├── SKILL.md
├── scripts/
│   ├── run_baseline.sh
│   ├── run_recon.sh
│   ├── run_auth_abuse.sh
│   ├── run_flood.sh
│   ├── run_slow_client.sh
│   ├── run_tls_checks.sh
│   └── summarize_results.py
└── references/
    ├── safety-guardrails.md
    ├── expected-outcomes.md
    └── remediation-map.md
```

## `SKILL.md` draft

```markdown
---
name: watchtower-attack-drill
description: Run controlled internet-style attack simulations against Watchtower nodes, collect evidence, score resilience, and produce prioritized hardening recommendations.
---

# Watchtower Attack Drill

Use this skill when the user asks to validate Watchtower resilience against basic internet attacks.

## Workflow

1. Read `references/safety-guardrails.md` and enforce legal/scope constraints.
2. Run `scripts/run_baseline.sh` and capture baseline metrics.
3. Run one scenario at a time:
   - `scripts/run_recon.sh`
   - `scripts/run_auth_abuse.sh`
   - `scripts/run_flood.sh`
   - `scripts/run_slow_client.sh`
   - `scripts/run_tls_checks.sh`
4. For each scenario, compare outputs against `references/expected-outcomes.md`.
5. Run `scripts/summarize_results.py` to produce a scorecard:
   - Prevent
   - Detect
   - Sustain
   - Recover
6. Map failures to fixes using `references/remediation-map.md`.
7. Return a concise report with:
   - evidence,
   - regressions,
   - release-blocker status,
   - prioritized next actions.

## Reporting contract

Always include:
- executed commands,
- outcome per scenario,
- failed checks marked as regressions,
- explicit stop/go recommendation for internet exposure.
```

## Script responsibilities

- `run_baseline.sh`: health probes and pre-attack service quality snapshot.
- `run_recon.sh`: `nmap` exposure scan and service fingerprint capture.
- `run_auth_abuse.sh`: controlled login abuse simulation with low-volume dictionaries.
- `run_flood.sh`: bounded-duration HTTP flood profile.
- `run_slow_client.sh`: slow-client socket exhaustion simulation.
- `run_tls_checks.sh`: TLS protocol/cipher and header posture checks.
- `summarize_results.py`: parse artifacts and output machine-readable + markdown scorecards.

## Acceptance criteria for the skill

1. Produces the same scorecard format every run.
2. Fails fast when safety preconditions are not met.
3. Marks any failed resilience check as a regression.
4. Produces remediation recommendations tied to failing controls.
5. Supports dry-run mode for command preview without traffic generation.

## Minimal first implementation backlog

1. Create `SKILL.md` with workflow and reporting contract.
2. Implement `run_baseline.sh`, `run_recon.sh`, and `run_tls_checks.sh` first.
3. Add `summarize_results.py` with JSON + markdown output.
4. Add auth/flood/slow-client scripts with conservative default limits.
5. Add CI linting for scripts (`shellcheck`, `ruff` for python summarizer).

## Alignment with existing playbook

The skill directly operationalizes the attack families and resilience scorecard documented in the [Watchtower internet attack simulation playbook](./watchtower-attack-simulation.md), so both human-led and Codex-led drills follow the same control objectives.
