# Release App

Provides utilities for packaging the project and uploading it to PyPI.

Package metadata and PyPI credentials are represented by simple dataclasses. The
`DEFAULT_PACKAGE` constant exposes the current project details while the
`Credentials` class can hold either an API token or a username/password pair for
Twine uploads.

The management command `build_pypi` wraps the release logic. Run it with `--all`
for the full workflow:

```bash
python manage.py build_pypi --all
```

Individual flags exist for incrementing the version, building the distribution
and uploading via Twine. See `--help` for details.
