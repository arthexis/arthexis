# PyPI installation verification

- Environment: Ubuntu container with Python 3.11 and pip 25.3 using a fresh virtual environment at `/tmp/pypi-test`.
- Steps:
  1. Created the virtual environment.
  2. Upgraded pip.
  3. Installed the `arthexis` package from PyPI.
  4. Ran `pip install --upgrade arthexis` to confirm the upgrade path.
- Results:
  - Installation completed successfully, installing `arthexis` 0.1.34 and all required dependencies without errors.
  - The upgrade command reported the package already satisfied at 0.1.34, showing the latest version is present and the upgrade path functions as expected.
  - No post-install or upgrade issues observed.
