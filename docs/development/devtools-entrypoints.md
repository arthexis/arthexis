# Developer script entrypoints

Developer launcher modules now live under `utils/devtools/`, while the executable entrypoints remain:

- `python scripts/test_server.py`
- `python scripts/migration_server.py`

Management commands continue to delegate to the same shared modules, so editors and shell scripts should keep targeting the `scripts/` entrypoints rather than importing an app-shaped package.
