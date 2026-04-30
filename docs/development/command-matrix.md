# Command Matrix

This matrix defines the allowed test and QA command paths for Arthexis contributors and agents.

## Canonical command path

For local app-targeted test runs, use:

```bash
.venv/bin/python manage.py test run -- <target>
```

Use this as the default in local QA and agent workflows.

## Migration graph policy

Maintain a **single canonical migrations graph only** under `apps/*/migrations/`; do not introduce parallel `migrations_v*` module trees.

## Allowed commands by context

| Context | Allowed command(s) | Notes |
| --- | --- | --- |
| Local QA (app tests) | `.venv/bin/python manage.py test run -- <target>` | Canonical path for app test execution. |
| Local QA (suite-wide/marker runs) | `.venv/bin/python manage.py test run -- -m "<expr>"` | Keep using the same management entrypoint; pass pytest args after `--`. |
| Local QA (migration validation) | `.venv/bin/python manage.py migrations check` | Preferred migration guardrail before PRs. |
| CI pipelines | `python -m pytest ...` inside workflow jobs | Valid in CI workflow implementation where jobs already manage interpreter/bootstrap lifecycle. |
| Initial local bootstrap | `./install.sh` or `install.bat` | Run when `.venv` is missing. The install entrypoints create `.venv` before migrations and environment refresh. |
| Troubleshooting missing deps | `./env-refresh.sh --deps-only` or `env-refresh.bat` | Run only after `.venv` already exists; then rerun the canonical command. |
| Troubleshooting command behavior | `.venv/bin/python manage.py test run -- <target> -k <pattern>` | Troubleshoot through the same canonical entrypoint first. |
| Direct local pytest | `.venv/bin/python -m pytest ...` | Allowed only for low-level debugging of pytest/plugin behavior or when developing pytest-backed helpers. Prefer recording reproductions with the canonical management command in PR notes. |

## Where direct `pytest` is valid

Direct `pytest` remains valid in two places:

1. CI workflow internals under `.github/workflows/`.
2. Implementation and maintenance of pytest-backed helper tooling (for example `apps/tests/management/commands/test.py` and `utils/devtools/test_server.py`).

Outside those cases, default to `.venv/bin/python manage.py test run -- <target>`.
