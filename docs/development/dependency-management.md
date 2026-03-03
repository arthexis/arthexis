# Dependency management

`pyproject.toml` is the canonical source for runtime dependencies via `[project].dependencies` and CI tooling dependencies via `[project.optional-dependencies].ci`.

`requirements.txt` and `requirements-ci.txt` are generated outputs and should not be edited manually.

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
