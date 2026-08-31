# CI Policy

GitHub Actions budget is treated as a scarce shared resource for the private
repository. Pull requests should run only the lightweight Linux sanity gate by
default.

## Automatic Gates

- `Linux CI` is the only workflow triggered by pull requests.
- `Linux CI / Linux sanity` runs on the local self-hosted Ubuntu runner labels
  `[self-hosted, Linux, X64, arthexis-ci]` and delegates to
  `./scripts/ci/linux-sanity.sh`.
- The self-hosted checkout must clean the whole workspace, including `.venv`.
  Pull request runs must not preserve executable virtualenv contents across
  workflow runs.
- The self-hosted runner installs native packages outside the workflow, so the
  workflow sets `ARTHEXIS_SKIP_SANITY_APT=1`.
- Workflow path filters intentionally ignore root Windows batch entrypoints.

The sanity script validates dependency metadata, performs the Linux install
smoke path, checks migrations and imports, runs critical Ruff rules, and runs
the workflow policy regression tests. The default install smoke refreshes
dependencies and checks the migration graph without applying every historical
migration. Use `ARTHEXIS_CI_INSTALL_SMOKE_DB_MODE=apply` only when a manual run
needs full migration application evidence.

## Manual And Release Workflows

Heavy workflows remain available for deliberate operator use, release work, or
default-branch evidence when budget is available:

- `Install Health Check`
- `CodeQL`
- `Secret Scan`
- `Security Scan`
- `Release Impact`
- `Release Simulator`
- `Prepare Release PR`
- `Release Upgrade Replay`
- housekeeping workflows such as cache cleanup, stale closure, and branch prune

These workflows should not be required for ordinary PR iteration.

`Install Health Check` is `workflow_dispatch` only. It does not run on pushes,
pull requests, or schedules, and it no longer opens or updates automation issues
from inside the workflow. Trigger it manually only when install evidence is
needed for release prep or a focused install regression investigation.

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
python -m pytest apps/core/tests/reports/test_release_publish_regressions.py::test_only_linux_ci_runs_on_pull_requests -q
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
