# Release install verification

Installation and upgrade paths are now validated as part of the main CI workflow rather than a separate lightweight check. This keeps coverage centralized and avoids duplicating effort.

## Coverage in CI
- Workflow: [CI](../.github/workflows/ci.yml)
- Triggers: All pushes, pull requests, and the nightly schedule.
- Environment: Ubuntu runner with Python 3.x.
- Steps:
  - **install** job bootstraps the suite with caching, runs `./install.sh --clean --no-start`, lints documentation links, validates migrations, checks import resolution, lints seed fixtures, and executes pytest.
  - **upgrade** job installs from the default branch, checks out the pull request changes, runs `./upgrade.sh --local --no-restart`, and repeats migration validation, import checks, fixture linting, and tests on the upgraded environment.

The consolidated CI run provides the same installation assurance while combining it with the broader validation suite that already runs on every change.
