# CI Troubleshooting

## `fatal: origin/main...HEAD: no merge base`

This Git error appears when the workflow tries to compute the merge base between the branch under test (`HEAD`) and the requested base ref (for example `origin/main`), but the local checkout does not contain a common ancestor commit. In GitHub Actions this typically happens when both refs are fetched with a very shallow history (for example `--depth=1`), so the shared merge base commit is not available locally.

To resolve it:
- Fetch the base ref with a larger depth or unshallow the repository (e.g. `git fetch origin "$BASE_REF" --deepen=50` or `git fetch --unshallow`) before running commands that need a merge base.
- Ensure `BASE_REF` points to the correct upstream branch and that your feature branch is descended from it.

Increasing the fetch depth restores the missing ancestor commit so `git merge-base origin/main HEAD` can succeed.

## App registry integrity check

Arthexis keeps settings module import lean by validating `PROJECT_LOCAL_APPS` wiring through the Django checks framework instead of importing every local app during settings import.

Run this check locally before opening a PR and in CI guardrail steps:

```bash
./scripts/preflight-env.sh
.venv/bin/python manage.py check --tag core
```

This catches:
- local app entries in `PROJECT_LOCAL_APPS` that are not importable
- `apps.*` entries in `INSTALLED_APPS` that are missing from `PROJECT_LOCAL_APPS` and `PROJECT_APPS`

## Screenshot coverage in CI

The screenshot workflow already captures baseline routes. To request additional CI screenshot coverage without taking manual screenshots:

- Add authenticated paths (admin/session-required pages) to `.github/screenshot-paths.authenticated.txt`.
- Add public paths to `.github/screenshot-paths.public.txt`.
- Keep one path per line and start with `/` (for example `/admin/links/reference/`).
- Blank lines and `#` comments are ignored.

These path files are read by `.github/workflows/dashboard-screenshot.yml`, which appends them to the default capture list and uploads the resulting screenshots as workflow artifacts.

### When local preview tooling is unavailable

If local preview generation fails because browser/screenshot tooling is unavailable in the current runtime, do not block on manual capture. Register the route for CI screenshot capture instead:

- Add the route (starting with `/`, one per line) to `.github/screenshot-paths.authenticated.txt` (requires login) or `.github/screenshot-paths.public.txt` (public route).
- Push the change and rely on the screenshot workflow artifacts as the preview source of truth.
- Prefer this CI route-registration flow over ad-hoc local browser setup in constrained environments.

## Debugger/autoreload duplicate startup logs

When running `manage.py runserver` with Django autoreload enabled, startup diagnostics can appear twice. This is expected: Django starts a watcher process and a child server process, and both emit startup output before the child keeps serving logs.

For development debugging sessions where duplicate startup logs are noisy:

- run with `--noreload` to use a single process
- or keep autoreload and set `DJANGO_SUPPRESS_MIGRATION_CHECK=1` to reduce repeated migration-check output

If initialization code must run only once, guard it to run in the child process only (for example by checking `RUN_MAIN == "true"`).
