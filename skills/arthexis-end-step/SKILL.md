---
name: arthexis-end-step
description: Trigger Arthexis end-of-turn effects after all main phases and before cleanup. Use when the user asks for `end step`, `end-step`, `turn`, `next turn`, or explicitly wants effects declared for the end of a turn to fire before cleanup.
---

# Arthexis End Step

Use this skill after all `$arthexis-main-phase` work for a turn is complete and before `$arthexis-cleanup-step`.

## Boundary

- Allowed: read the active turn boundary state, mark declared end-of-turn effects as triggered, and report what fired.
- Not allowed: package changes, service changes, process termination, network/firewall edits, database writes, credential changes, branch changes, GitHub mutations, destructive cleanup, or any action that might affect `wlan1`.
- End effects are structured notes declared during the turn. They are not arbitrary shell commands.

## Workflow

1. Re-read `/home/arthe/AGENTS.md` and `/home/arthe/workgroup.txt` when coordination may matter.
2. Run the end step:

```bash
python3 /home/arthe/.codex/skills/arthexis-cleanup-step/scripts/turn_boundary.py end-step
```

3. Report the turn id, triggered effect count, and any effect notes.
4. Continue immediately into `$arthexis-cleanup-step` for explicit `turn` or `next turn`.

## Declaring End Effects

During a turn, an agent may declare a note to trigger at end step:

```bash
python3 /home/arthe/.codex/skills/arthexis-cleanup-step/scripts/turn_boundary.py declare-effect --name "effect-name" --source "source" --note "what should be considered at end step"
```

The end step marks these effects as triggered and writes an event log under `/home/arthe/.local/state/arthexis-turn/events.jsonl`.
