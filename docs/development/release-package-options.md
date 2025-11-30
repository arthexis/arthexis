# Release Package Configuration

The release workflow reads package metadata from `apps.core.release.Package` instances.
Administrators can override the default behavior through the `apps.core.Package`
model, which now exposes additional optional fields:

- **Version path** – relative path to the version file that should be updated
  during a build. When left blank the release process uses the repository root
  `VERSION` file.
- **Dependencies path** – relative path to the requirements file used while
  generating the temporary `pyproject.toml`. Defaults to `requirements.txt`
  when the field is empty.
- **Test command** – custom command executed when the `build` helper runs the
  test suite. The command is parsed with `shlex.split` and executed via
  `subprocess.run`. When unset, the workflow runs `python manage.py test`.

These settings allow packaging projects with non-standard layouts without
modifying the release tooling. Every value is optional, so existing packages
continue to behave exactly as before. To update the settings, edit the package
record in the Django admin and provide the appropriate paths or command.
