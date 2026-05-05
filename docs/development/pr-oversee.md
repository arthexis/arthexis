# Pull Request Oversight CLI

Use `pr_oversee` when PR supervision needs deterministic state, blockers, and local commands instead of prose-only inspection.

```bash
.venv/bin/python manage.py pr_oversee --repo arthexis/arthexis inspect --pr 123 --json
.venv/bin/python manage.py pr_oversee --repo arthexis/arthexis gate --pr 123
.venv/bin/python manage.py pr_oversee --repo arthexis/arthexis comments --pr 123 --unresolved --json
.venv/bin/python manage.py pr_oversee --repo arthexis/arthexis checkout --pr 123 --worktree ../arthexis-pr-123 --branch repos-pr-123
.venv/bin/python manage.py pr_oversee --repo arthexis/arthexis test-plan --pr 123 --json
.venv/bin/python manage.py pr_oversee --repo arthexis/arthexis ci --pr 123 --failures --logs --json
.venv/bin/python manage.py pr_oversee --repo arthexis/arthexis dependency-dedupe --json
.venv/bin/python manage.py pr_oversee --repo arthexis/arthexis merge --pr 123 --write --method squash --delete-branch
.venv/bin/python manage.py pr_oversee --repo arthexis/arthexis cleanup --pr 123 --write --worktree ../arthexis-pr-123
.venv/bin/python manage.py pr_oversee --repo arthexis/arthexis hygiene --pr 123
```

The command delegates GitHub state and merge operations to `gh`, but normalizes the output into stable JSON. `gate` exits nonzero when a PR is blocked by draft state, merge state, review state, failed or pending checks, or unresolved review threads. `merge` always re-runs the gate immediately before calling `gh pr merge`, and `cleanup` refuses to remove local artifacts unless the PR is already merged.
