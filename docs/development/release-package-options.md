# Release Package Configuration

The release workflow reads package metadata from `apps.release.models.Package`
instances and converts them to the runtime release dataclass
`apps.release.services.models.Package` via `Package.to_package()`.
Administrators can override the default behavior through the
`apps.release.models.Package` model fields:

- **Version path** – relative path to the version file that should be updated
  during a build (`version_path`). When left blank, the release process uses the
  repository root `VERSION` file.
- **Dependencies path** – relative path to the requirements file used while
  generating the temporary `pyproject.toml` (`dependencies_path`). Defaults to
  `requirements.txt` when the field is empty.
- **Test command** – custom command executed when the release build runs the
  test suite (`test_command`). The command is parsed with `shlex.split` and
  executed via `subprocess.run`. When unset, the workflow runs
  `.venv/bin/python manage.py test`.

These settings allow packaging projects with non-standard layouts without
modifying the release tooling. Every value is optional, so existing packages
continue to behave exactly as before. To update the settings, edit the package
record in the Django admin and provide the appropriate paths or command.

## Where this is used

- `apps/release/management/commands/release.py`:
  `release build` resolves a package selection and passes the package to the
  release build pipeline.
- `apps/release/services/builder.py`:
  `build()` reads `package.version_path`, `package.dependencies_path`, and
  `package.test_command` to perform version updates, dependency loading, and
  test execution.
- `apps/release/models/package.py`:
  `Package.to_package()` maps Django model fields to the runtime
  `apps.release.services.models.Package` object consumed by the build/publish
  workflow.
