# Pull Request Oversight CLI

Use `pr_oversee` when PR supervision needs deterministic state, blockers, and local commands instead of prose-only inspection.

```bash
.venv/bin/python manage.py pr_oversee --repo arthexis/arthexis --json inspect --pr 123
.venv/bin/python manage.py pr_oversee --repo arthexis/arthexis gate --pr 123
.venv/bin/python manage.py pr_oversee --repo arthexis/arthexis --json comments --pr 123 --unresolved
.venv/bin/python manage.py pr_oversee --repo arthexis/arthexis checkout --pr 123 --worktree ../arthexis-pr-123 --branch repos-pr-123
.venv/bin/python manage.py pr_oversee --repo arthexis/arthexis --json test-plan --pr 123
.venv/bin/python manage.py pr_oversee --repo arthexis/arthexis --json ci --pr 123 --failures --logs
.venv/bin/python manage.py pr_oversee --repo arthexis/arthexis --json dependency-dedupe
.venv/bin/python manage.py pr_oversee --repo arthexis/arthexis merge --pr 123 --write --method squash --delete-branch
.venv/bin/python manage.py pr_oversee --repo arthexis/arthexis cleanup --pr 123 --write --worktree ../arthexis-pr-123
.venv/bin/python manage.py pr_oversee --repo arthexis/arthexis hygiene --pr 123
.venv/bin/python manage.py pr_oversee --repo arthexis/arthexis --json monitor --pr 123 --interval 30 --max-iterations 120
.venv/bin/python manage.py pr_oversee --repo arthexis/arthexis --json monitor --pr 123 --merge --cleanup --write --delete-branch
.venv/bin/python manage.py pr_oversee --repo arthexis/arthexis --json watch --pr 123 --interval 30 --max-iterations 120
.venv/bin/python manage.py pr_oversee --repo arthexis/arthexis --json watch --pr 123 --background --expected-head-sha <sha>
```

The command delegates GitHub state and merge operations to `gh`, but normalizes the output into stable JSON. `gate` exits nonzero when a PR is blocked by draft state, merge state, review state, failed or pending checks, or unresolved review threads. `merge` always re-runs the gate immediately before calling `gh pr merge` and passes a head-commit guard to GitHub, and `cleanup` refuses to remove local artifacts unless the PR is already merged.

`monitor` runs the oversight workflow in a controlled loop: inspect, gate, review comments, hygiene, changed-file test plan, CI summary, and dependency duplicate scan. It sleeps only while checks are pending. It stops with `manualDecisionRequired` for review blockers, hygiene failures, failed local validation, missing write permission for requested write actions, or loop limits, and exits nonzero in that state. By default it is read-only; use `--merge --write` to merge a ready PR and add `--cleanup` for post-merge cleanup. Set `--max-iterations 0` or `--timeout 0` to disable those caps.

When `--run-test-plan` is used with `--worktree`, monitor creates or reuses the worktree and syncs it to the observed PR head before running local validation so the checks run against the current PR checkout. Changed files are cached by PR head commit across polling iterations, and dependency duplicate scanning is performed once per monitor run instead of once per poll.

`watch` is intentionally passive. It polls `inspect`/`gate` state only, writes a JSON state file under `work/pr-watch` by default, and stops on deterministic success (`ready` or `merged`) or deterministic failure (`closed`, head SHA drift, failed checks, conversation blockers, or loop/timeout limits). It waits on pending checks and review approval instead of running local commands or touching GitHub state. On Windows, `watch --background` detaches a hidden child process and defaults to a dismissible Windows dialog when the watcher exits; when the PR URL is present, the dialog includes a `Go to PR` button. Add `--no-notify-windows` to suppress that notification.
