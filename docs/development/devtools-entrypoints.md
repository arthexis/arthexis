# Developer devtools entrypoints

Developer launcher modules live under `scripts/devtools/` and should be invoked through their module entrypoints directly from the repository root so the checkout is available on `sys.path`:

- `cd /path/to/arthexis && .venv/bin/python -m scripts.devtools.migration_server`

For cron jobs, systemd units, editor tasks, and other non-interactive launchers, set the working directory to the checkout root before calling these commands. The removed compatibility shims inferred the repository root automatically, but the module entrypoints require the repository root to already be the current working directory.

Editor configs and shell scripts should target these module entrypoints directly rather than compatibility shims in the repository root.

For regular local app-test execution, prefer the canonical command path:

- `.venv/bin/python manage.py test run -- <target>`

Use direct `pytest` only for devtool/helper maintenance where the implementation itself is pytest-backed.
