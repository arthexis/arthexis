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
- Create a virtual environment and install dependencies:
  ```bash
  python -m venv .venv
  source .venv/bin/activate
  pip install -r requirements.txt
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
- Run **MyPy** through the root developer workflow for the approved incremental rollout:
  ```bash
  make mypy
  ```
  This shells through `scripts/run_mypy.sh` so local runs and CI share the same noise-filtered, Django-aware invocation.
  The repo-root `pyproject.toml` is the source of truth for the enforced MyPy baseline under
  `[tool.mypy].files`, and the current approved paths are:
  - `scripts/generate_requirements.py`
  - `scripts/sort_pyproject_deps.py`
  - `apps/protocols/`
  - `apps/repos/github.py`
  - `apps/repos/services/github.py`
  - `apps/core/services/health.py`
  - `apps/core/services/health_checks.py`
  - `apps/core/modeling/`
  - `apps/core/system_ui.py`
  The local `.pre-commit-config.yaml` hook and the dedicated GitHub Actions MyPy workflow are
  intentionally scoped to those validated paths so excluded apps stay out of the blocking
  rollout until their own adoption step is complete.
- When MyPy needs a suppression, prefer the narrowest possible scope: fix the types first,
  then use a targeted `[[tool.mypy.overrides]]` entry or a line-level ignore with the exact
  error code and a short reason. Avoid broad package-wide ignores for convenience, and record
  persistent rollout debt in `docs/development/mypy-adoption-checklist.md`.
- Treat **Pyright** and **MyPy** as complementary signals here: Pyright remains the wider,
  editor-friendly import and flow-analysis pass, while MyPy is the blocking, Django-aware
  enforcement gate for the explicitly approved rollout paths. Keep both green when touching
  shared modules.
- Prefer clear, lean functions over clever shortcuts. Add comments or docstrings only when they preserve important context, explain unconventional choices, or feed user-facing/generated help text.

### Documentation changes
- Documentation lives under `docs/` and uses MkDocs.
- Preview changes locally:
  ```bash
  mkdocs serve
  ```

## Testing
Run tests before opening a PR:
```bash
pytest
```

Useful subsets:
- Default CI subset (exclude slow & integration):
  ```bash
  pytest -m "not slow and not integration"
  ```
- Targeted module or file:
  ```bash
  pytest apps/ocpp/tests/test_example.py
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
