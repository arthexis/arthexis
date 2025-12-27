# Release install verification

The previous manual PyPI install report tracked a single installation against version 0.1.34. To keep the documentation aligned with current releases, Arthexis relies on an automated workflow that verifies installation and upgrade paths on every change that could alter packaging.

## Automated workflow
- Workflow: [Install latest release](../.github/workflows/release-install.yml)
- Triggers: Pushes or pull requests that modify migrations or the workflow itself.
- Environment: Ubuntu runner with Python 3.x.
- Steps:
  - **install** job checks out the code, sets up Python, and runs `bash install.sh --terminal` to validate a clean installation.
  - **upgrade** job installs the latest `main` branch via `install.sh`, then checks out the pull request and runs `./upgrade.sh --local --no-restart` to confirm the upgrade path succeeds.

These automated checks ensure installation guidance stays current without relying on historical one-off reports.
