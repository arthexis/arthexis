# Developer devtools entrypoints

Developer launcher modules under `utils/devtools/` should be invoked through the module entrypoints directly.

Run these commands from the repository root, or otherwise ensure the checkout root
is on `sys.path`, because `python -m utils.devtools...` resolves `utils` from the
current working directory:

- `.venv/bin/python -m utils.devtools.test_server`
- `.venv/bin/python -m utils.devtools.migration_server`

Editor configs and shell scripts should target these module entrypoints directly rather than the removed compatibility shims in `scripts/`.
