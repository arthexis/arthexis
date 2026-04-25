---
name: arthexis-cleanup-step
description: Finish an Arthexis turn after end-step by waiting for turn-owned processes to complete and terminating lingering registered turn-owned processes after up to 10 minutes. Use when the user asks for `cleanup step`, `cleanup-step`, `turn`, `next turn`, or turn-end process cleanup.
---

# Arthexis Cleanup Step

Use this skill at the very end of a turn, after `$arthexis-end-step`.

## Boundary

- Cleanup may wait for and terminate only registered turn-owned processes.
- It must not kill unrelated host processes, stop services, edit packages, modify repositories, write application databases, change network policy, reboot, or affect `wlan1`.
- If a process was not registered as turn-owned, report it as outside cleanup authority instead of terminating it.

## Workflow

1. Re-read `~/AGENTS.md` and `~/workgroup.txt` when coordination may matter.
2. Run cleanup with the default 10-minute wait:

```bash
python3 ~/.codex/skills/arthexis-cleanup-step/scripts/turn_boundary.py cleanup-step --timeout 600
```

3. Report the turn id, wait timeout, turn-owned process count, termination count, and archive path.
4. For explicit multi-turn runs, rest between turns only after cleanup completes.

## Turn State

Start a tracked turn before untap:

```bash
python3 ~/.codex/skills/arthexis-cleanup-step/scripts/turn_boundary.py start-turn --label "turn-label"
```

Register long-running process IDs that were intentionally started during a turn:

```bash
python3 ~/.codex/skills/arthexis-cleanup-step/scripts/turn_boundary.py register-pid <pid> --label "why it is turn-owned"
```

Cleanup verifies the registered PID identity before acting, includes descendants of registered processes, waits up to the timeout for clean exit, sends `SIGTERM` to lingering registered processes, and archives completed turn state under `~/.local/state/arthexis-turn/turns/`.
