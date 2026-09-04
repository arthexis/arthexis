# CI Policy

GitHub Actions budget is treated as a shared resource. Pull requests should run
the lightweight Linux sanity gate by default, with security scans providing
additional evidence where configured.

## Automatic Gates

- `Linux CI / Linux sanity` runs on every pull request so it can safely be used
  as a required status check for `main`. Its pull-request `paths:` filter is the
  catch-all `**`, which preserves the workflow-policy regression contract while
  ensuring GitHub creates the check for every PR. Do not narrow this filter: if
  GitHub skips a required workflow entirely, the check can remain `Expected`
  indefinitely.
- Pushes to `main` and `release/**` keep selective path filters so irrelevant
  pushes do not consume the self-hosted runner.
- `Linux CI / Linux sanity` runs on the local self-hosted Ubuntu runner labels
  `[self-hosted, Linux, X64, arthexis-ci]` and delegates to
  `./scripts/ci/linux-sanity.sh`.
- The self-hosted checkout must clean the whole workspace, including `.venv`.
  Pull request runs must not preserve executable virtualenv contents across
  workflow runs.
- The self-hosted runner installs native packages outside the workflow, so the
  workflow sets `ARTHEXIS_SKIP_SANITY_APT=1`.
- `Install Health Check` runs after every push to `main` and remains manually
  dispatchable. It is post-merge/default-branch evidence, not a pull-request
  gate, so it should not be added to the required status checks for `main`.
- Install Health runs in explicit Debian 13 and Ubuntu 24.04 containers on
  GitHub-hosted Linux runners. Python 3.13 is the primary runtime and carries
  the full SQLite pytest shards plus PostgreSQL install coverage; Python 3.11
  keeps minimum-supported-version install evidence.
- Every Install Health matrix entry removes `.venv` before running the normal
  `./install.sh --no-start` entry point. Pip downloads may be cached, but the
  installed environment itself is always rebuilt from a clean repository.

The repository ruleset for the default branch should require
`Linux CI / Linux sanity` before merging. This keeps pull requests blocked while
the sanity job is queued or running and prevents merge after a failed sanity
run.

The sanity script validates dependency metadata, performs the Linux install
smoke path, checks migrations and imports, runs critical Ruff rules, and runs
the workflow policy regression tests. The default install smoke refreshes
dependencies and checks the migration graph without applying every historical
migration. Use `ARTHEXIS_CI_INSTALL_SMOKE_DB_MODE=apply` only when a manual run
needs full migration application evidence.

Install Health provides the deeper default-branch install invariant: it starts
native Redis (and PostgreSQL where selected) inside each Linux job environment,
checks dependency metadata, performs a fresh default installer invocation,
refreshes dependencies, validates imports and migrations, creates the docs admin,
checks installed app manifests and import contracts, and runs the full pytest
corpus split into OCPP and non-OCPP shards on the primary Debian/Python runtime.
Release simulation requires a successful Install Health run for the current
`main` SHA, so automatic post-main execution keeps that release evidence current.

## Manual And Release Workflows

Heavy workflows remain available for deliberate operator use, release work, or
default-branch evidence when budget is available:

- `CodeQL`
- `Secret Scan`
- `Security Scan`
- `Release Impact`
- `Release Simulator`
- `Prepare Release PR`
- `Release Upgrade Replay`
- housekeeping workflows such as cache cleanup, stale closure, and branch prune

These workflows should not be required for ordinary PR iteration unless the
repository policy is deliberately tightened and their triggers are adjusted so
the corresponding required checks are reported for every applicable PR.

`Install Health Check` intentionally does not run on pull requests or schedules,
and it does not open or update automation issues from inside the workflow. Its
automatic trigger is limited to pushes that produce a new `main` revision; use
`workflow_dispatch` when install evidence needs to be regenerated manually for
release prep or a focused regression investigation.

## Local Linux Validation

Use the Ubuntu development session or WSL for focused local reproduction:

```bash
cd /path/to/arthexis
./scripts/ci/linux-sanity.sh
```

The script installs native Linux dependencies and starts Redis when needed. A
fresh local Linux environment therefore needs passwordless `sudo`, or the
operator must install/start those dependencies first and rerun with:

```bash
ARTHEXIS_SKIP_SANITY_APT=1 ./scripts/ci/linux-sanity.sh
```

For focused test work after the environment exists:

```bash
source .venv/bin/activate
python -m pytest apps/core/tests/reports/test_release_publish_regressions.py::test_linux_ci_and_security_scans_run_on_pull_requests -q
python -m ruff check .github scripts apps/core/tests/reports/test_release_publish_regressions.py
```

Run broader checks manually only when the change warrants the local runtime cost.

## Self-Hosted Runner

The repository runner named `ARTH-THINKPAD` is started manually for PR batches
that need self-hosted Linux checks. It should not be configured to auto-start at
Windows logon. When online, it must advertise these labels:

```text
self-hosted
Linux
X64
arthexis-ci
```

The host must already have the Linux sanity dependencies and Redis service
installed:

```bash
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
  ca-certificates curl git python3-venv redis-server \
  libcairo2 libgdk-pixbuf-2.0-0 libpango-1.0-0 \
  libpangocairo-1.0-0 shared-mime-info
sudo systemctl enable --now redis-server || sudo service redis-server start
```

Start and stop the runner from the Windows coordinator session:

```powershell
C:\Users\arthexis\agent-tools\arthexis-runner.cmd status
C:\Users\arthexis\agent-tools\arthexis-runner.cmd start
C:\Users\arthexis\agent-tools\arthexis-runner.cmd stop
```

Verify the runner before relying on PR checks:

```bash
gh api repos/arthexis/arthexis/actions/runners \
  --jq '.runners[] | {name, status, busy, labels: [.labels[].name]}'
```

Trigger the reduced gate from GitHub without GitHub-hosted runner minutes:

```bash
gh workflow run "Linux CI" --repo arthexis/arthexis --ref main
gh run list --repo arthexis/arthexis --workflow "Linux CI" --limit 5
```
