# Contributing to Arthexis Constellation

Thanks for helping improve the Arthexis Constellation suite! This guide summarizes the
expected workflows for contributing across the Django apps, scripts, and documentation
in this repository.

## Code of conduct
Be respectful and collaborative. When in doubt, default to a kind and professional tone
in issues, pull requests, and reviews.

## Project layout
- **apps/**: Django apps that power the suite.
- **config/**: Django project settings and shared configuration.
- **docs/**: MkDocs-powered documentation and operational playbooks.
- **scripts/** + root `*.sh`/`*.bat`: Installer, lifecycle, and upgrade tooling.
- **tests/**: Cross-cutting test helpers and fixtures.

## Getting started
### 1. Environment
- Python 3.10+ is required.
- Create a virtual environment and install dependencies (runtime + QA extras):
  ```bash
  python -m venv .venv
  source .venv/bin/activate
  ./.venv/bin/pip install -r requirements.txt
  ./.venv/bin/pip install '.[qa]'
  ```

### Command style convention
- Default to `.venv/bin/python manage.py ...` for Django commands in docs and local development.
- If the instance is already running and the task is an operator-facing runtime action, prefer `./command.sh ...`.
- Windows exception: use `command.bat ...` for runtime operations and keep using the repository `*.bat` lifecycle scripts where documented.
- Managed-script exception: when a guide points to a dedicated script (`install.sh`, `start.sh`, `upgrade.sh`, etc.), prefer that script over direct `manage.py`.

### 2. Run the suite locally
You can run in Terminal mode with the provided scripts (recommended for parity with
production tooling):
```bash
./install.sh --terminal
./start.sh
```

Alternatively, use the Django CLI:
```bash
.venv/bin/python manage.py migrate
.venv/bin/python manage.py runserver 127.0.0.1:8888
```

## Development workflow
### Branching and commits
- Create a topic branch per change.
- Keep commits focused and descriptive.
- Reference relevant issues or docs when possible.

### Django changes
- Add or update tests alongside app changes.
- When you touch models:
  1. Run `.venv/bin/python manage.py makemigrations`.
  2. Review the migration file for correctness.
  3. Apply migrations with `.venv/bin/python manage.py migrate`.
  4. Note any data migrations or backfills in your PR description.

### Formatting and style
- Run **Ruff** checks before opening a PR (required):
  ```bash
  python -m ruff check --select E9 .
  ```
- Use **Black** for Python formatting:
  ```bash
  python -m black .
  ```
- Check import resolution with the canonical standalone checker:
  ```bash
  make check-imports
  ```
- Run **Pyright** for editor-friendly import diagnostics across the broader repository:
  ```bash
  pyright
  ```
- Prefer clear, lean functions over clever shortcuts. Add comments or docstrings only when they preserve important context, explain unconventional choices, or feed user-facing/generated help text.

### Documentation changes
- Documentation lives under `docs/` and uses MkDocs.
- Follow `docs/development/documentation-governance.md` for doc type placement, archive-vs-update decisions, and PR review expectations.
- Preview changes locally:
  ```bash
  mkdocs serve
  ```

## Testing
Install test dependencies, then run tests before opening a PR:
```bash
./.venv/bin/pip install -r requirements.txt
./.venv/bin/pip install '.[qa]'
./env-refresh.sh --deps-only
./.venv/bin/python manage.py test run
```

`manage.py test run` now begins with a QA readiness step before any targeted pytest execution. It reports:
- virtualenv presence,
- the Python executable path used for the run,
- core test dependency availability (`pytest`, `pytest-django`, `pytest-timeout`, `pytest-asyncio`).

If any core dependency is missing, the command fails fast before attempting any tests.

`requirements.txt` is intentionally runtime-only, so `pytest` may be missing until the QA extras are installed. See [Dependency management](docs/development/dependency-management.md) for details.

Useful subsets:
- Default CI subset (exclude slow & integration):
  ```bash
  ./.venv/bin/python manage.py test run -- -m "not slow and not integration"
  ```
- Targeted module or file:
  ```bash
  ./.venv/bin/python manage.py test run -- apps/ocpp/tests/test_example.py
  ```

If a test requires system services (Redis, Postgres, etc.), mention the dependency
in the PR and document any setup steps you used.

## Pull request checklist
- [ ] Tests pass locally (or note what is skipped and why).
- [ ] Migrations are included and applied (if applicable).
- [ ] Documentation updates for user-facing changes.
- [ ] Follow-up tasks are captured in the PR description or linked issue.

## Security and secrets
- Do **not** commit secrets or production credentials.
- Use environment variables or the existing configuration patterns in `config/`.

## Getting help
- Open a draft PR for early feedback.
- Reference related docs in `docs/development/` when describing build or release steps.

Thank you for contributing!
