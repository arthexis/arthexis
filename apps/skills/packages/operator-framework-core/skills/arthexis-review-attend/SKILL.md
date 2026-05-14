---
name: arthexis-review-attend
description: Attend one Arthexis GitHub pull request review loop end to end. Use when the operator asks Codex to work a numbered issue or PR, work a PR review blocker, attend comments, fix requested changes, pick another PR to work meanwhile, resolve review threads, push a PR update, hand a PR back to pr_oversee monitor, merge after deterministic gates pass, or clean up patchwork after a merged PR.
---

# Arthexis Review Attend

## Quick Start

Use this skill to take exactly one PR through review feedback, validation, push, thread resolution, monitor, optional merge, and cleanup. Start with live GitHub state; do not rely on stale conversation state.

When the operator gives a number such as `work 7711`, resolve it first and skip candidate selection:

```powershell
python "$env:CODEX_HOME\skills\arthexis-review-attend\scripts\review_thread.py" resolve-target --repo arthexis/arthexis --number <number>
```

- `PullRequest`: use that number directly in the PR review loop below.
- `Issue`: treat that issue as the chosen implementation target; inspect it with `gh issue view <number>`, create a dedicated branch or patchwork checkout from `main`, implement the scoped fix, push, and open a PR linked to the issue.

```powershell
.venv\Scripts\python.exe manage.py pr_oversee --repo arthexis/arthexis --json inspect --pr <number>
.venv\Scripts\python.exe manage.py pr_oversee --repo arthexis/arthexis --json comments --unresolved --pr <number>
.venv\Scripts\python.exe manage.py pr_oversee --repo arthexis/arthexis --json test-plan --pr <number>
```

If the operator did not name a number or PR, choose one in this order:

1. Open PR with substantive unresolved review threads and no unrelated local conflict.
2. Open PR that is mergeable and blocked only by deterministic gates.
3. Open issue with small, independent implementation scope.

Avoid mixing issue work and PR-review work in one worktree state.

## Review Loop

1. Refresh live PR state with `inspect`, `comments --unresolved`, `ci`, `hygiene`, and `test-plan`.
2. If there are substantive unresolved comments, create or reuse the PR patchwork checkout:

```powershell
.venv\Scripts\python.exe manage.py pr_oversee checkout --pr <number>
```

`checkout` links the current checkout `.venv` into the patchwork worktree by default so generated validation commands can run there. Use `--no-link-venv` only when the target checkout must be isolated from the local environment.

3. Patch only the requested behavior and directly related tests. Ignore nitpicks only when the operator explicitly authorized that.
4. Run the generated test plan. If the plan is too broad or platform-blocked, run the closest changed-app/file target and record the exact blocker.
5. Commit only relevant files. Keep `.arthexis-pr-oversee.json`, `.venv` junctions, local DBs, and generated worktree metadata out of commits.
6. Push to the PR head branch, then refresh unresolved comments.
7. Reply to the addressed thread with a terse change and validation summary, then resolve it.
8. Rerun `gate`. If all deterministic blockers clear and the operator request covers merge work, run monitor with an expected head SHA.

```powershell
.venv\Scripts\python.exe manage.py pr_oversee --repo arthexis/arthexis --json monitor --pr <number> --expected-head-sha <sha> --max-iterations 120 --interval 30 --merge --write --delete-branch
```

9. After merge, run patchwork read-only first, then prune only monitor-owned merged/closed candidates.

```powershell
.venv\Scripts\python.exe manage.py pr_oversee --repo arthexis/arthexis --json patchwork
.venv\Scripts\python.exe manage.py pr_oversee --repo arthexis/arthexis --json patchwork --write
```

If Windows leaves a merged patchwork directory behind after `patchwork --write` because it is no longer a Git worktree but still contains `.venv` junction or local residue, switch to `$arthexis-pr-oversee` and use its `remove_patchwork_residue.py` fallback. Do not hand-write recursive deletion unless that fallback is unavailable.

## Review Thread Helper

Use `scripts/review_thread.py` when raw GitHub GraphQL would otherwise be rewritten in chat.

List review threads:

```powershell
python "$env:CODEX_HOME\skills\arthexis-review-attend\scripts\review_thread.py" list --repo arthexis/arthexis --pr <number>
```

Reply and resolve an addressed thread:

```powershell
python "$env:CODEX_HOME\skills\arthexis-review-attend\scripts\review_thread.py" summary --commit <sha> --change "Handled requested behavior" --validation "test command passed" > .\review-reply.txt
python "$env:CODEX_HOME\skills\arthexis-review-attend\scripts\review_thread.py" reply-resolve --thread-id <thread-id> --body-file .\review-reply.txt
```

Use `summary` or `manage.py pr_oversee reply-summary` to avoid hand-writing repeated reply text. Use `--body "..."` for short replies. Prefer `--body-file` for multi-line validation summaries.

## Guardrails

- Keep the main checkout clean; do review patches in `ARTHEXIS_PATCHWORK_DIR` or the suite patchwork default unless there is a specific reason not to.
- Use exact head SHA guards before merging after a push.
- Do not claim ready, merged, or blocked without a fresh live `pr_oversee` or `gh pr view` check.
- Treat unresolved requested changes as blockers even if CI is green.
- Treat pending CI as a wait state, not success.
- If patchwork cleanup fails because of local `.venv` junctions or metadata created during validation, rerun `pr_oversee patchwork --write` first; it now prunes suite-owned residue after Git reports a partially removed worktree.
