# Developer devtools entrypoints

Developer launcher modules under `utils/devtools/` should be invoked through the module entrypoints directly from the repository root so the checkout is available on `sys.path`:

- `cd /path/to/arthexis && .venv/bin/python -m utils.devtools.test_server`
- `cd /path/to/arthexis && .venv/bin/python -m utils.devtools.migration_server`

For cron jobs, systemd units, editor tasks, and other non-interactive launchers, set the working directory to the checkout root before calling these commands. The removed `scripts/*.py` shims inferred the repository root automatically, but the module entrypoints require the repository root to already be the current working directory.

Editor configs and shell scripts should target these module entrypoints directly rather than the removed compatibility shims in `scripts/`.
