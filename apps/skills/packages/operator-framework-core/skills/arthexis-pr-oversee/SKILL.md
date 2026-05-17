---
name: arthexis-pr-oversee
description: Refresh Arthexis GitHub PR state, list all open PRs with top priority suggestions, identify merge-ready work, inspect reviews/checks/conflicts, handle substantive review comments, merge safe PRs, and find superseded dependency bumps. Use when the operator asks to oversee PRs, suggest the next PRs to work, check readiness, merge green PRs, work review comments, or clean up dependency PRs.
---

# Arthexis PR Oversee

## Quick Start

Use this skill for GitHub PR supervision around the Arthexis suite. Always refresh live PR state before claiming a PR is ready or merged.

Default source-backed read-only pass from an Arthexis checkout:

```powershell
.venv\Scripts\python.exe manage.py pr_oversee --repo arthexis/arthexis --json inspect --pr <number>
.venv\Scripts\python.exe manage.py pr_oversee --repo arthexis/arthexis --json gate --pr <number>
.venv\Scripts\python.exe manage.py pr_oversee --repo arthexis/arthexis --json advance --include-drafts
.venv\Scripts\python.exe manage.py pr_oversee --repo arthexis/arthexis --json dependency-dedupe
.venv\Scripts\python.exe manage.py pr_oversee --repo arthexis/arthexis --json patchwork
```

Use `--json` when another tool needs machine-readable output.

For continuous oversight, use `$arthexis-pr-oversee-monitor` and its `monitor` or passive `watch` workflow.

## Priority Suggestions

When the operator asks for all open PRs, top PRs, or what to work next, run the priority helper before choosing:

```powershell
.venv\Scripts\python.exe manage.py pr_oversee --repo arthexis/arthexis --json advance --include-drafts
python "$env:CODEX_HOME\skills\arthexis-pr-oversee\scripts\pr_priority_suggestions.py" --repo arthexis/arthexis --top 3
```

The suite `advance` command returns every considered open PR plus `topSuggestions`, and `--include-drafts` keeps drafts in the ranking instead of silently skipping them. It ranks merge-ready PRs first, otherwise-ready drafts second, then current unresolved review threads, failing CI, pending checks, merge conflicts, and lower-action states. Use the Python helper only when the checkout does not yet include the Django command. Use `--no-review-threads` only when speed matters more than precise review-blocker detection.

To let the command perform deterministic writes instead of only planning them:

```powershell
.venv\Scripts\python.exe manage.py pr_oversee --repo arthexis/arthexis --json advance --include-drafts --ready-drafts --write
.venv\Scripts\python.exe manage.py pr_oversee --repo arthexis/arthexis --json advance --merge --write --delete-branch
```

## Acting

- Treat `gate`, `inspect`, `comments --unresolved`, `ci`, `hygiene`, and `test-plan` as the normal first pass.
- Use `advance --include-drafts` to reduce manual all-open-PR ranking and to avoid forgetting drafts during oversight.
- Use `merge --write` or `monitor --merge --write` only after reading blockers and confirming the PRs are within the requested scope.
- Inspect substantive review comments in the checkout before patching. Ignore nitpicks only when the operator has already said to do so.
- Put temporary PR worktrees under `ARTHEXIS_PATCHWORK_DIR`; `checkout` defaults there when `--worktree` is omitted, and `monitor --run-test-plan` does the same.
- Run `patchwork` read-only before pruning; add `--write` only after confirming it lists monitor-owned merged or closed worktrees.
- If `patchwork --write` leaves a merged/closed patchwork directory behind on Windows with `Invalid argument` or junction-related cleanup errors, first verify it is no longer a Git worktree, then use the residue fallback script below. Keep this fallback scoped to explicit PR numbers or explicit paths under `ARTHEXIS_PATCHWORK_DIR`.
- Do not reset or clean a dirty local checkout as part of PR oversight.

## Source Commands

- `inspect`: return a complete PR state snapshot, review threads, files, commits, and readiness.
- `gate`: fail unless deterministic readiness gates pass.
- `comments --unresolved`: list unresolved review threads that need human or code action.
- `checkout`: create an isolated PR worktree and write `.arthexis-pr-oversee.json` metadata.
- `test-plan`: infer local validation commands from changed files.
- `ci --logs`: collect failed or pending CI checks with optional log snippets.
- `dependency-dedupe`: find duplicate or superseded dependency PR groups.
- `advance --include-drafts`: rank open PRs, return top suggestions, and optionally mark ready drafts or merge ready PRs with `--write`.
- `reply-summary`: format a terse review-thread reply body from change and validation bullets.
- `hygiene`: check for deterministic PR hygiene issues such as generated files or missing migrations.
- `merge --write`: gate and merge a PR.
- `cleanup --write`: remove merged worktrees or branches after verification.
- `monitor`: run the whole workflow until completion or manual decision.
- `watch`: passively poll PR state until ready/merged success or deterministic failure; `watch --background` detaches a hidden Windows-friendly watcher and defaults to a dismissible Windows notification.
- `patchwork --write`: prune monitor-owned patchwork worktrees for merged or closed PRs, with stale-open pruning only when explicitly requested.

## Windows Patchwork Residue Fallback

Use this only after `pr_oversee patchwork --write` was already attempted and the target PR is merged or closed. It refuses active Git worktrees and refuses paths outside the patchwork root.

```powershell
python "$env:CODEX_HOME\skills\arthexis-pr-oversee\scripts\remove_patchwork_residue.py" --pr <number>
python "$env:CODEX_HOME\skills\arthexis-pr-oversee\scripts\remove_patchwork_residue.py" --pr <number> --write
```

For multiple known merged PRs, pass `--pr` repeatedly. Use the dry run first and rerun `pr_oversee patchwork` after cleanup.

## Script Fallback

Use the bundled scripts only when the checkout does not yet include the Django command:

```powershell
python "$env:CODEX_HOME\skills\arthexis-pr-oversee\scripts\pr_oversee.py" list --repo arthexis/arthexis
python "$env:CODEX_HOME\skills\arthexis-pr-oversee\scripts\pr_oversee.py" merge-ready --repo arthexis/arthexis
python "$env:CODEX_HOME\skills\arthexis-pr-oversee\scripts\pr_dependency_pair.py" --repo arthexis/arthexis
```
