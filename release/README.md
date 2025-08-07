# Release App

Provides utilities for packaging the project and uploading it to PyPI.

Package metadata and PyPI credentials are represented by simple dataclasses. The
`DEFAULT_PACKAGE` constant exposes the current project details while the
`Credentials` class can hold either an API token or a username/password pair for
Twine uploads.

For convenience the `PackageConfig` model stores this information in the
database. It is exposed in the Django admin where an action can invoke the
release workflow using the stored metadata and credentials.

The management command `build_pypi` wraps the release logic. Run it with `--all`
for the full workflow:

```bash
python manage.py build_pypi --all
```

Individual flags exist for incrementing the version, building the distribution
and uploading via Twine. See `--help` for details.

Use the `--test` flag to execute the project's tests before building. Test
output is stored in the `TestLog` model and can be reviewed or purged from the
Django admin.
