---
name: arthexis-cleanup-step
description: Finish an Arthexis turn after end-step by waiting for turn-owned processes, terminating lingering registered turn-owned processes after up to 10 minutes, and applying the adaptive 10-minute turn cadence rest unless the user asked to go again immediately. Use when the user asks for `cleanup step`, `cleanup-step`, `turn`, `next turn`, or turn-end process cleanup.
---

# Arthexis Cleanup Step

Use this skill at the very end of a turn, after `$arthexis-end-step`.

## Boundary

- Cleanup may wait for and terminate only registered turn-owned processes.
- The cadence rest is recorded as an expiry for the remaining part of the 10-minute turn cycle; time already spent during the turn is deducted.
- It must not kill unrelated host processes, stop services, edit packages, modify repositories, write application databases, change network policy, reboot, or affect `wlan1`.
- If a process was not registered as turn-owned, report it as outside cleanup authority instead of terminating it.

## Workflow

1. Re-read `~/AGENTS.md` and `~/workgroup.txt` when coordination may matter.
2. Run cleanup with the default 10-minute process wait and adaptive 10-minute turn cadence:

```bash
python3 ~/.codex/skills/arthexis-cleanup-step/scripts/turn_boundary.py cleanup-step --timeout 600 --cadence 600
```

3. If the user has explicitly commanded the device to go again immediately, skip only the cadence rest for the current turn:

```bash
python3 ~/.codex/skills/arthexis-cleanup-step/scripts/turn_boundary.py cleanup-step --timeout 600 --cadence 600 --skip-cadence-rest
```

4. Report the turn id, process wait timeout, turn-owned process count, termination count, cadence seconds, elapsed turn seconds before rest, cadence rest seconds, whether cadence rest was skipped, the cadence-rest expiry, and archive path.
5. For explicit multi-turn runs, do not add a separate fixed sleep after cleanup; the adaptive cadence rest is recorded as state with an expiry so the tool returns promptly.

## Turn State

Start a tracked turn before untap:

```bash
python3 ~/.codex/skills/arthexis-cleanup-step/scripts/turn_boundary.py start-turn --label "turn-label"
```

Register long-running process IDs that were intentionally started during a turn:

```bash
python3 ~/.codex/skills/arthexis-cleanup-step/scripts/turn_boundary.py register-pid <pid> --label "why it is turn-owned"
```

Cleanup verifies the registered PID identity before acting, includes descendants of registered processes, waits up to the timeout for clean exit, sends `SIGTERM` to lingering registered processes, records any cadence rest requested by `--cadence` under `~/.local/state/arthexis-turn/cadence-rest.json`, and archives completed turn state under `~/.local/state/arthexis-turn/turns/`.
