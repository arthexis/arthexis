# Developer devtools entrypoints

Developer launcher modules under `utils/devtools/` should be invoked through the module entrypoints directly:

- `.venv/bin/python -m utils.devtools.test_server`
- `.venv/bin/python -m utils.devtools.migration_server`

Editor configs and shell scripts should target these module entrypoints directly rather than the removed compatibility shims in `scripts/`.
