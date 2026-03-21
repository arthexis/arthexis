# MyPy adoption checklist

Use this checklist to expand Django-aware MyPy coverage in a monotonic way.

## Stable baseline first

The current MyPy-owned paths are recorded in `pyproject.toml` under `[tool.mypy].files` so coverage only grows by explicit review:

- `scripts/generate_requirements.py`
- `scripts/sort_pyproject_deps.py`
- `apps/protocols/`
- `apps/repos/github.py`
- `apps/repos/services/github.py`
- `apps/core/services/health.py`
- `apps/core/services/health_checks.py`
- `apps/core/modeling/`
- `apps/core/system_ui.py`

Django-aware checking is enabled through the `mypy_django_plugin.main` plugin with `config.settings` as the settings module. Keep new ownership additions small enough that `mypy` stays green before adding the next path.

## Rollout order per app

Enable modules in this order for each app:

1. service and helper modules
2. pure domain modules
3. managers and query helpers
4. views, admin, and forms
5. models last, once ORM typing can be handled without broad suppression

## Narrow overrides only

Prefer targeted overrides for specific integration pain points instead of excluding an app forever.

Current targeted override:

- `psutil`: ignored until stub coverage is added for uptime-oriented helpers.

When another integration needs an override, record the exact module or dependency and the reason beside the config change.

## Annotation backlog before enablement

Search these patterns before claiming a package as MyPy-owned:

- `TYPE_CHECKING`
- `from typing import Any`
- unparameterized `list`, `dict`, `set`, and `tuple` annotations

### `apps/protocols/`

Backlog summary:

- `TYPE_CHECKING`: 0
- `from typing import Any`: 0
- unparameterized collections: 6

Small follow-ups:

- Parameterize protocol spec dictionaries in `apps/protocols/services.py`.
- Replace runtime tuple and set checks in `apps/protocols/registry.py` with narrower aliases where practical.

### `apps/repos/`

Backlog summary:

- `TYPE_CHECKING`: 8
- `from typing import Any`: 5
- unparameterized collections: 22

Small follow-ups:

- Service-heavy candidates: `apps/repos/release_management.py` and `apps/repos/views/webhooks.py`.
- Registry and model-adjacent candidates: `apps/repos/task_utils.py` and `apps/repos/models/repositories.py`.
- Replace `Any` payloads with `TypedDict` shapes for webhook and GitHub CLI data as those modules become owned.

### Selected `apps/core/` modules

Backlog summary for the current service and registry-heavy targets:

- `TYPE_CHECKING`: 0
- `from typing import Any`: 2
- unparameterized collections: 7

Small follow-ups:

- `apps/core/modeling/events.py`
- `apps/core/modeling/registry.py`
- `apps/core/services/odoo_quote_report.py`

Keep broader `apps/core/system/ui/` rollout tracked separately until translation and uptime helpers are typed cleanly.

## Regression tracking during rollout

If a rollout candidate fails MyPy repeatedly across runs, keep it in the checklist as a tracked regression instead of silently removing it from scope.

Current tracked regressions:

- `apps/core/system/ui/`: lazy translation return types and uptime payload shapes still fail Django-aware MyPy checks.

When you hit a persistent failure:

1. rerun the failing target
2. fix obvious local typing issues
3. record the remaining blocker here with the failing module path and reason

That keeps ownership monotonic while making the remaining debt explicit.
