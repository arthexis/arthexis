# PyPI installation verification

- Environment: Ubuntu container with Python 3.12.12 and pip 25.3 using a fresh virtual environment at `/tmp/arthexis-pypi-test`.
- Steps:
  1. Created the virtual environment.
  2. Upgraded pip.
  3. Installed the `arthexis` package from PyPI.
- Results:
  - Installation completed successfully, installing `arthexis` 0.1.33 and all required dependencies without errors.
  - `pip show arthexis` reports the package under `/tmp/arthexis-pypi-test/lib/python3.12/site-packages` with the expected metadata.
  - No post-install issues observed.
