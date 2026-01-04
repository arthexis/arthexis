# CI performance playbook

We target full CI runs that finish in five minutes or less on GitHub hosted runners. The workflow now parallelizes pytest collection and execution with [`pytest-xdist`](https://pypi.org/project/pytest-xdist/), and uses the shared `PYTEST_ADDOPTS` environment variable in `.github/workflows/ci.yml` so both the install and upgrade jobs run with the same tuned flags.

## Guardrails for future changes

- Keep `PYTEST_ADDOPTS` aligned with the five-minute goal. Any additional plugins or flags should maintain parallel execution (`-n auto --dist loadfile`) and avoid disabling worker reuse.
- When adding new test suites, ensure they are shard-friendly (tests should not rely on global ordering or shared mutable state) so xdist can keep the wall-clock under five minutes.
- Avoid removing cache steps or virtualenv reuse in the workflow unless there is a replacement that preserves the same or better runtime characteristics.
- If a CI run begins to exceed five minutes, prefer optimizing slow tests or adding marks/fixtures that let them run in parallel rather than increasing timeouts.

## Local verification

To mirror CI locally, install dependencies and run:

```bash
./install.sh --no-start
source .venv/bin/activate
pytest
```

Pytest will automatically honor the `PYTEST_ADDOPTS` defined in the workflow, so local runs measure the same parallelized configuration used in CI.
