# Release App

Provides utilities for packaging the project and uploading it to PyPI.

The management command `build_pypi` wraps the release logic. Run it with `--all`
for the full workflow:

```bash
python manage.py build_pypi --all
```

Individual flags exist for incrementing the version, building the distribution
and uploading via Twine. See `--help` for details.
