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

### 2. Run the suite locally
You can run in Terminal mode with the provided scripts (recommended for parity with
production tooling):
```bash
./install.sh --terminal
./start.sh
```

Alternatively, use the Django CLI:
```bash
python manage.py migrate
python manage.py runserver 0.0.0.0:8888
```

## Development workflow
### Branching and commits
- Create a topic branch per change.
- Keep commits focused and descriptive.
- Reference relevant issues or docs when possible.

### Django changes
- Add or update tests alongside app changes.
- When you touch models:
  1. Run `python manage.py makemigrations`.
  2. Review the migration file for correctness.
  3. Apply migrations with `python manage.py migrate`.
  4. Note any data migrations or backfills in your PR description.

### Formatting and style
- Use **Black** for Python formatting:
  ```bash
  python -m black .
  ```
- Prefer clear, well-documented functions over clever shortcuts.

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
