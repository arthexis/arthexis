# Dependency management

`pyproject.toml` is the canonical source for dependency declarations. `[project].dependencies` contains the production runtime set, while `[project.optional-dependencies]` now uses smaller role-oriented extras such as `preview`, `test`, `docs`, `hardware`, `video`, `nodes`, and `ci`.

Generated `requirements-*.txt` files provide locked install profiles for the same roles. They should not be edited manually. `requirements-runtime.txt` intentionally tracks only the production runtime set so installs that consume it stay slim. `requirements-ci.txt` remains the superset used by CI validation jobs, but it is now an explicit CI-only profile rather than the default lookup source for local capability installs.

## Dependency groups

- `requirements-runtime.txt`: runtime-only packages for production installs and other execution environments that only need to run Arthexis.
- `.[preview]`: browser automation packages used by preview and screenshot workflows.
- `.[test]`: pytest and related test runner packages.
- `.[docs]`: documentation toolchain packages.
- `.[hardware]`: GPIO, RFID, and LCD support for hardware-capable nodes.
- `.[video]`: camera and WebRTC support packages.
- `.[nodes]`: optional node-facing packages such as Graphviz, QR generation, and FTP services.
- `.[ci]`: CI-only validation and packaging tools such as linting, typing, and release helpers.
- `requirements-preview.txt`, `requirements-test.txt`, `requirements-docs.txt`, `requirements-hw.txt`, `requirements-video.txt`, and `requirements-nodes.txt`: generated capability profiles that combine runtime dependencies with one focused extra.
- `requirements-ci.txt`: generated superset of runtime plus every optional dependency group for CI and other full-validation environments.

Install optional groups explicitly when needed. Examples:

```bash
pip install '.[test]'
pip install '.[preview]'
pip install '.[hardware]'
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

To verify the generated requirements files match committed output:

```bash
python scripts/generate_requirements.py --check
```

or:

```bash
make requirements-check
```

CI runs this check and fails when either committed file differs from generated output.
