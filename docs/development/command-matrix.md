# Command Matrix

This matrix defines the allowed test and QA command paths for Arthexis contributors and agents.

## Canonical command path

For local app-targeted test runs, use:

```bash
.venv/bin/python manage.py test run -- <target>
```

Use this as the default in local QA and agent workflows. For opt-in reusable environments, prefer the wrapper form shown below so the same command works with either a checkout-local `.venv` or a shared cache selected by `ARTHEXIS_ENV_ROOT`.


## Fast contributor bootstrap

Use `./dev-env.sh` when you want a contributor-facing setup shortcut before
running the canonical test commands. The helper uses the native local
virtualenv/install path by default: it checks for a dependency-warmed shared
virtualenv, then a checkout-local `.venv`, and finally falls back to the normal
`./install.sh` path.

Supported fast paths:

- **Local path:** set `ARTHEXIS_ENV_ROOT` to a shared cache root or
  `ARTHEXIS_VENV_DIR` to an exact virtualenv path. `./py` resolves those before
  repo-local `.venv`/`venv`, so commands such as
  `ARTHEXIS_ENV_ROOT="$HOME/.cache/arthexis" ./py manage.py check` can reuse a
  warmed environment.

Use full `./install.sh` setup when you need to create or repair the project
virtualenv, refresh service/runtime locks, perform first-run migrations, or
validate installer behavior. Fast bootstrap keeps the existing canonical test
commands intact; use `.venv/bin/python manage.py test run -- <target>` for the
repo-local environment or `./py manage.py test run -- <target>` when the virtualenv
location may come from a shared cache.

## Migration graph policy

Maintain a **single canonical migrations graph only** under `apps/*/migrations/`; do not introduce parallel `migrations_v*` module trees.

## Opt-in reusable local environments

Checkout-local `.venv` remains the default. To opt into a reusable virtual environment cache, set `ARTHEXIS_ENV_ROOT` before running the normal lifecycle entrypoints:

```bash
ARTHEXIS_ENV_ROOT="$HOME/.cache/arthexis" ./install.sh --no-start
ARTHEXIS_ENV_ROOT="$HOME/.cache/arthexis" ./env-refresh.sh --deps-only
ARTHEXIS_ENV_ROOT="$HOME/.cache/arthexis" ./py manage.py check
```

The resolver prefers an explicit `ARTHEXIS_VENV_DIR`, then a dependency-hash keyed virtual environment under `ARTHEXIS_ENV_ROOT/venvs/`, then the checkout-local `.venv` fallback. The cache key includes the active Python major/minor version, platform and architecture, dependency manifests, `pyproject.toml`, and installer helper scripts that affect dependency installation. `env-refresh.sh` also reinstalls the active checkout editably after dependency refresh so shared environments import the repository that invoked the command.

## Allowed commands by context

| Context | Allowed command(s) | Notes |
| --- | --- | --- |
| Local QA (app tests) | `.venv/bin/python manage.py test run -- <target>` | Canonical path for app test execution. |
| Local QA (app tests, venv-agnostic wrapper) | `./py manage.py test run -- <target>` or `py.bat manage.py test run -- <target>` | Honors `ARTHEXIS_VENV_DIR` and the `ARTHEXIS_ENV_ROOT` dependency-hash cache before repo-local `.venv`/`venv`; prints bootstrap guidance when no environment exists. |
| Local QA (suite-wide/marker runs) | `.venv/bin/python manage.py test run -- -m "<expr>"` | Keep using the same management entrypoint; pass pytest args after `--`. |
| Local QA (migration validation) | `.venv/bin/python manage.py migrations check` | Preferred migration guardrail before PRs. |
| CI pipelines | `python -m pytest ...` inside workflow jobs | Valid in CI workflow implementation where jobs already manage interpreter/bootstrap lifecycle. |
| Initial local bootstrap | `./install.sh` or `install.bat` | Run when the local environment is missing. By default this creates `.venv`; with `ARTHEXIS_ENV_ROOT`, Unix lifecycle scripts create or reuse the keyed cache environment. |
| Troubleshooting missing deps | `./env-refresh.sh --deps-only` or `env-refresh.bat` | Run after bootstrap; Unix `env-refresh.sh` honors `ARTHEXIS_VENV_DIR`/`ARTHEXIS_ENV_ROOT` and falls back to `.venv`. Then rerun the canonical command. |
| Troubleshooting command behavior | `.venv/bin/python manage.py test run -- <target> -k <pattern>` | Troubleshoot through the same canonical entrypoint first. |
| Direct local pytest | `.venv/bin/python -m pytest ...` | Allowed only for low-level debugging of pytest/plugin behavior or when developing pytest-backed helpers. Prefer recording reproductions with the canonical management command in PR notes. |

## Where direct `pytest` is valid

Direct `pytest` remains valid in two places:

1. CI workflow internals under `.github/workflows/`.
2. Implementation and maintenance of pytest-backed helper tooling (for example `apps/tests/management/commands/test.py` and `utils/devtools/test_server.py`).

Outside those cases, default to `.venv/bin/python manage.py test run -- <target>`.
