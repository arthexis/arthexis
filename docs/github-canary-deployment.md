# GitHub Canary Deployment Setup

This guide configures this device as a self-hosted deployment target for the
GitHub environment named `canary`.

## What is added in this repository

- Workflow: `.github/workflows/deploy-canary.yml`
- Deploy script: `scripts/github-canary-deploy.sh`
- Runner bootstrap script: `scripts/setup-github-runner-canary.sh`

The workflow deploys on:

- pushes to `main`
- manual dispatch (`workflow_dispatch`) with optional `ref` and `dry_run`

The workflow expects a self-hosted runner with labels:

- `self-hosted`
- `linux`
- `canary`

## 1. Create the GitHub environment

In the GitHub repository:

1. Open **Settings -> Environments**.
2. Create an environment named `canary`.
3. Add environment variable `CANARY_DEPLOY_PATH` with value:
   `/home/arthe/canary`

Optional:

- Add required reviewers for deployment approvals.
- Add wait timers and branch restrictions.

## 2. Register this device as a self-hosted runner

From `/home/arthe/arthexis`, run:

```bash
chmod +x scripts/setup-github-runner-canary.sh scripts/github-canary-deploy.sh
RUNNER_URL="https://github.com/<owner>/<repo>" \
RUNNER_TOKEN="<registration-token>" \
./scripts/setup-github-runner-canary.sh
```

Notes:

- Get `<registration-token>` from **Settings -> Actions -> Runners -> New self-hosted runner**.
- The default labels include `canary`, which the workflow requires.

## 3. Run a deployment

### Automatic

- Push to `main`; GitHub will run `Deploy Canary`.

### Manual

- Open **Actions -> Deploy Canary -> Run workflow**.
- Set `ref` (branch, tag, or commit) and optional `dry_run=true`.

## 4. Deployment behavior

`scripts/github-canary-deploy.sh` will:

1. Use `CANARY_DEPLOY_PATH` (default `/home/arthe/canary`).
   If omitted, the workflow and script fall back to `/home/arthe/canary`.
2. Fetch from `origin` and resolve the requested ref.
3. Refuse deployment if tracked local changes are present.
4. Checkout the target commit (detached HEAD).
5. Run `./upgrade.sh --local --start --no-warn`.
6. Run `./status.sh` when available.
