# Dependency management

`pyproject.toml` is the canonical source for runtime dependencies via `[project].dependencies`.

`requirements.txt` is generated output for deployment compatibility and should not be edited manually.

## Regenerate requirements

Use either command:

```bash
python scripts/generate_requirements.py
```

or:

```bash
make requirements
```

## Validate generated requirements

To verify `requirements.txt` matches generated output:

```bash
python scripts/generate_requirements.py --check
```

or:

```bash
make requirements-check
```

CI runs this check and fails when the committed file differs from generated output.
