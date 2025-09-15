# Agent Guidelines
- Note: do not modify README files unless directed. Use Django's admindocs for app documentation.
- Application documentation lives in Django's admindocs. Do not create or modify per-app README files.
- Do not edit the changelog manually. An automated process builds it from commit
  messages.
- Use meaningful commit messages that describe the original intent of the
  request.
- Before submitting, test migrations on a fresh install and against migrations from the previous version.
- Run `pre-commit run --all-files` to ensure migration checks pass. Use `env-refresh.sh --clean` for a fresh install.
- Starting with release 0.1.9, do not rewrite existing migrations. Always create new migrations when changes are needed.
- Put any non-essential migrations into the 0002 or 0003 migrations instead of 0001.
- Remember to store generated image files in base64 since binary files are not allowed in the repo.
- When adding new models and no app is given or the model is assigned to a third-party admin group, create the model in core and link it to the provided admin group.
- Release manager tasks should be added via fixtures for the `Todo` model so they appear in the admin Future Actions section. Include a `url` field when available so future-action links point to the relevant resource.
- After modifying a view or any part of the GUI, add a `Todo` fixture titled `Validate screen [Screen]` and set its `url` to the screen needing manual validation.
- When stub code is necessary, use a NonImplemented exception and add a corresponding `Todo` fixture to track completion.
- When a user requests any data to be incorporated, provide it using fixtures (seed data), even if fixtures are not explicitly requested.
- Store each fixture object in its own file to avoid merge conflicts, giving each file a unique name related to the item it contains.
- Fixtures must not include numeric primary keys; use natural keys instead.
  - awg.CableSize: (awg_size, material)
  - awg.ConduitFill: (trade_size, conduit)
  - awg.CalculatorTemplate: name
  - core.Package: name
  - core.ReleaseManager: (user.username, package.name)
  - core.PackageRelease: (package.name, version)
- Avoid committing empty fixtures.
- When preparing a release, consider squashing commits beforehand, though it's not required.
- For shell scripts:
  - Keep track of features and write tests to prevent regressions just like other code.
  - Follow consistent naming conventions, using the `.sh` extension with kebab-case names.
  - Keep the interface and meaning of flags consistent across scripts.
  - Ensure all shell scripts are executable. Verify new or modified scripts retain the `chmod +x` permission.

