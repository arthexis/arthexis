# Dependency management

`pyproject.toml` is the canonical source for dependency declarations. `[project].dependencies` contains the production runtime set, while optional groups such as `[project.optional-dependencies].dev`, `.preview`, and `.qa` hold non-runtime tools for local debugging, previews, and test execution.

`requirements.txt` and `requirements-ci.txt` are generated outputs and should not be edited manually. `requirements.txt` intentionally tracks only the production runtime set so installs that consume it stay slim. `requirements-ci.txt` remains the superset used by CI validation jobs.

## Dependency groups

- `requirements.txt`: runtime-only packages for production installs, Docker images, and other execution environments that only need to run Arthexis.
- `.[dev]`: local debugging helpers such as `django-debug-toolbar`.
- `.[preview]`: browser automation packages used by preview and screenshot workflows.
- `.[qa]`: pytest and related test runner packages.
- `requirements-ci.txt`: generated superset of runtime plus every optional dependency group for CI and other full-validation environments.

Install optional groups explicitly when needed. Examples:

```bash
pip install '.[qa]'
pip install '.[preview]'
```

## Regenerate requirements files

Use either command:

```bash
python scripts/generate_requirements.py
```

or:

```bash
make requirements
```

## Validate generated requirements files

To verify `requirements.txt` and `requirements-ci.txt` match generated output:

```bash
python scripts/generate_requirements.py --check
```

or:

```bash
make requirements-check
```

CI runs this check and fails when either committed file differs from generated output.
