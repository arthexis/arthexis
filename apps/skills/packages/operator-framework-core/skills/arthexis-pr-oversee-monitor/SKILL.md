---
name: arthexis-pr-oversee-monitor
description: Run the Arthexis `manage.py pr_oversee monitor` loop for GitHub pull requests until the PR merges, waits on checks, or stops for a manual decision. Use when the operator asks to oversee a specific PR continuously, run the full PR oversight toolchain, merge after deterministic gates pass, or diagnose why the monitor stopped.
---

# Arthexis PR Oversee Monitor

## Quick Start

Run from an Arthexis checkout with the suite virtualenv available. Prefer the source-backed Django command over the older skill scripts.

Read-only one-iteration snapshot:

```powershell
.venv\Scripts\python.exe manage.py pr_oversee --repo arthexis/arthexis --json monitor --pr <number> --max-iterations 1 --interval 0
```

Controlled merge loop after the operator has asked to oversee or merge the PR:

```powershell
.venv\Scripts\python.exe manage.py pr_oversee --repo arthexis/arthexis --json monitor --pr <number> --max-iterations 120 --interval 30 --merge --write --delete-branch
```

Use an isolated worktree when local validation or review fixes may be needed:

```powershell
.venv\Scripts\python.exe manage.py pr_oversee --repo arthexis/arthexis --json monitor --pr <number> --run-test-plan
```

The monitor defaults `--run-test-plan` worktrees to `ARTHEXIS_PATCHWORK_DIR` or the platform home patchwork directory, with deterministic names such as `arthexis-arthexis-pr-7662`.

## Workflow

1. Start with a read-only monitor pass and inspect `manualDecisionReasons`, `last.gate.blockers`, `last.inspect.pullRequest.reviewThreads`, `last.ci.failures`, and `last.hygiene`.
2. If the monitor stops for unresolved review threads, inspect the substantive comments and patch them in an isolated worktree.
3. Run the test plan the command reports. If a generated plan is too broad for the local platform, run the narrower changed-file targets and record the platform blocker exactly.
4. Push fixes to the PR head branch, then rerun the monitor. Use `--expected-head-sha <sha>` when guarding against a moving PR head matters.
5. Add `--merge --write --delete-branch` only after the gate is ready and the operator request covers merge work. The monitor should stop with `merge_decision_required` instead of merging when write mode is not present.
6. After merge, run patchwork hygiene in read-only mode, then prune with `--write` when only monitor-owned merged or closed worktrees are candidates.

## Related Commands

Use these commands for focused diagnosis when the monitor stops:

```powershell
.venv\Scripts\python.exe manage.py pr_oversee --repo arthexis/arthexis --json inspect --pr <number>
.venv\Scripts\python.exe manage.py pr_oversee --repo arthexis/arthexis --json comments --pr <number> --unresolved
.venv\Scripts\python.exe manage.py pr_oversee --repo arthexis/arthexis --json ci --pr <number> --logs
.venv\Scripts\python.exe manage.py pr_oversee --repo arthexis/arthexis --json hygiene --pr <number>
.venv\Scripts\python.exe manage.py pr_oversee --repo arthexis/arthexis --json test-plan --pr <number>
.venv\Scripts\python.exe manage.py pr_oversee --repo arthexis/arthexis --json patchwork
```

## Guardrails

- Always refresh live GitHub state before saying a PR is ready or merged.
- Treat `manual_decision_required` as a controlled stop, not as a tool failure.
- Keep `.arthexis-pr-oversee.json`, local `.venv` junctions, and other worktree metadata out of commits; patchwork cleanup may remove worktrees when only those owned files are dirty.
- Do not reset or clean a dirty checkout as part of the monitor workflow.
- The monitor can wait on checks, sync a reused worktree, run validation, merge, and clean up; it cannot decide away substantive review comments or missing human approvals.
