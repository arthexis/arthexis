---
name: operator-framework-align
description: Audit and align a node with the Arthexis SKILLS, AGENTS, and HOOKS model by checking local skill metadata, generated AGENTS context, hook inventory, retired operator-manual/workgroup references, and stale agent-role/personality language. Use when updating framework rules or local environment.
---

# Operator Framework Align

## Quick Start

Use this skill when local Codex state or the suite checkout must match the Arthexis SKILLS, AGENTS, and HOOKS model.

Read-only audit:

```powershell
python "$env:CODEX_HOME\skills\operator-framework-align\scripts\framework_audit.py" --repo [CONF.BASE_DIR] --codex-home $env:CODEX_HOME
```

Write the local console AGENTS file only when asked:

```powershell
python "$env:CODEX_HOME\skills\operator-framework-align\scripts\local_agents_sync.py" --write
```

## Rules

- Treat `OPERATOR` as the human using an LLM-assisted session.
- Treat `AGENT` as suite-provided context selected by node role and features, not as a personality, nickname, or workgroup role.
- Keep node-role-originating rules higher priority than general suite guidance.
- Keep skills flat by unique name. Lint name plus description against the 720 character RFID-card target.
- Do not require the retired operator manual or workgroup bookkeeping from local console context.
- Use hooks for deterministic actions and skills for workflows where judgment or routing remains useful.

## Scripts

- `scripts/framework_audit.py`: run the local AGENTS, skill catalog, hooks, and retired-language checks together.
- `scripts/skill_catalog_lint.py`: validate local skill names and description lengths.
- `scripts/local_agents_sync.py`: preview or write aligned console `AGENTS.md`.
- `scripts/hooks_audit.py`: list local and repo hook surfaces for deterministic Codex integration.
