# Agent Guidelines
- Note: do not modify README files unless directed. Use Django's admindocs for app documentation.
  - When a README change is requested, update the translated README files in `locale/` (for example `README.es.md`, `README.fr.md`, `README.ru.md`) to keep them in sync.
  - If a requested change would make any README content inaccurate or outdated, update the README and translations accordingly even if the user did not explicitly ask for README edits.
- Avoid creating new files or directories at the repository root unless the user specifically requests them.
- Application documentation lives in Django's admindocs. Do not create or modify per-app README files.
- Do not edit the changelog manually. An automated process builds it from commit
  messages.
- Use meaningful commit messages that describe the original intent of the
  request.
- Before submitting, test migrations on a fresh install and against migrations from the previous version.
- Starting with release 0.1.9, do not rewrite existing migrations. Always create new migrations when changes are needed.
- Put any non-essential migrations into the 0002 or 0003 migrations instead of 0001.
- Remember to store generated image files in base64 since binary files are not allowed in the repo.
- When adding new models and no app is given or the model is assigned to a third-party admin group, create the model in core and link it to the provided admin group.
- Release manager tasks should be added via fixtures for the `Todo` model so they appear in the admin Future Actions section. Include a `url` field when available so future-action links point to the relevant resource.
- When recently added or updated features require end-user validation, create a new Release manager `Todo` fixture describing the manual scenario so the change is exercised before release.
  - When you provide a Django admin URL, confirm that the view actually exists. Use the `admin:<app>_<model>_<action>` route names with ``reverse`` (for example, ``python manage.py shell -c "from django.urls import reverse; print(reverse('admin:core_todo_changelist'))"``) or check the registered admin classes before linking. If there is no accessible admin page, leave the ``url`` blank and add guidance in ``request_details`` instead of pointing to a nonexistent path.
- Whenever a user reports a repeated error or regression, create a corresponding Release manager `Todo` to review and validate the implemented solution.
- Follow the UI and visual design guidelines in `DESIGN.md` when making any interface changes.
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

## Agent Advice

Use the following checklist when deciding whether a change fixes a bug or merely alters configuration defaults:

1. **Crashes or Exceptions** – Does the current behavior raise an unhandled exception or prevent a supported workflow from completing?
2. **Functional Regression** – Has a feature stopped meeting documented requirements or reasonable user expectations?
3. **Security Issue** – Does the behavior expose sensitive data or create an unsafe default?
4. **Data Loss/Corruption** – Can users lose or corrupt data by following supported workflows?
5. **Build/Test Failure** – Does the project fail to build, install dependencies, or run required automated tests?
6. **Severe Performance Regression** – Is a workflow effectively unusable due to resource leaks or unacceptable slowness?

Configuration changes that simply prefer one valid default over another (for example, forcing `DEBUG=1` for `manage.py runserver`) do **not** fall into these bug categories because the original behavior still functions and supports legitimate use cases. Treat such changes as feature requests and only apply them when explicitly requested by stakeholders.

- Feature-driven tests must use `@pytest.mark.feature("<slug>")` where the slug
  matches a `nodes.NodeFeature`. CI uses these markers to include the
  feature-specific suites for each node role.
