# MyPy adoption checklist

Use this checklist to expand Django-aware MyPy coverage in a monotonic way.

## Incremental typing conventions

Use these conventions when tightening types in existing modules so MyPy gains
signal without forcing a large rewrite.

### Preferred shapes

- Model external payloads with `TypedDict` when the code expects named keys from
  subprocesses, HTTP APIs, webhooks, or serializer-like data.
- Describe pluggable adapters and callback contracts with `Protocol` instead of
  `Any` or bare duck-typed comments.
- Prefer `X | None` for nullable values.
- Introduce small module-local type aliases for repeated shapes owned by that
  module.
- Narrow command and collection inputs to `Mapping[str, object]`,
  `Sequence[str]`, or similar concrete abstractions when callers do not require
  mutability.

### Adoption notes

- Replace `Any` only when it currently blocks a useful MyPy check or obscures a
  stable data shape.
- Keep aliases close to the owning module unless the same shape is shared
  broadly.
- Add or refresh docstrings only when they materially improve discoverability,
  capture non-obvious typing intent, or feed user-facing/generated help text.
  Prefer lean code over routine explanatory boilerplate.
- Favor incremental changes in lower-dynamic modules first, then expand app by
  app as coverage improves.
- Record rollout sequencing, owned paths, and regressions in this document
  whenever MyPy coverage expands.

## Stable baseline first

The current MyPy-owned paths are recorded in `pyproject.toml` under `[tool.mypy].files` so coverage only grows by explicit review:

- `scripts/generate_requirements.py`
- `scripts/sort_pyproject_deps.py`
- `apps/protocols/`
- `apps/repos/github.py`
- `apps/repos/models/repositories.py`
- `apps/repos/services/github.py`
- `apps/repos/task_utils.py`
- `apps/core/services/health.py`
- `apps/core/services/health_checks.py`
- `apps/core/services/odoo_quote_report.py`
- `apps/core/modeling/`
- `apps/core/system_ui.py`
- `apps/core/system/ui/formatting.py`
- `apps/core/system/ui/network_probe.py`
- `apps/core/system/ui/services.py`
- `apps/core/system/ui/uptime.py`

Django-aware checking is enabled through the `mypy_django_plugin.main` plugin with `config.settings` as the settings module. Use the same environment assumptions as `scripts/run_mypy.sh` when invoking MyPy directly:

- `DJANGO_SETTINGS_MODULE=config.settings`
- `DJANGO_SECRET_KEY=mypy-secret-key`
- `ARTHEXIS_DISABLE_CELERY=1`
- `CELERY_LOG_LEVEL=WARNING`
- `DEBUG=0`

That keeps MyPy aligned with the canonical Django settings package without maintaining a dedicated shim module. Keep new ownership additions small enough that `mypy` stays green before adding the next path.

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

Treat backlog counts as on-demand metrics, not fixed targets in this checklist. Recompute them when reviewing rollout scope changes.

### `apps/protocols/`

Small follow-ups:

- Parameterize protocol spec dictionaries in `apps/protocols/services.py`.
- Replace runtime tuple and set checks in `apps/protocols/registry.py` with narrower aliases where practical.

### `apps/repos/`

Small follow-ups:

- Service-heavy candidates: none currently pending after `apps/repos/views/webhooks.py` moved into the MyPy-owned path set.
- Recently completed: `apps/repos/release_management.py` now uses narrow `TypedDict` payloads for release/issue/PR data and is included in the MyPy approved path set.
- Recently completed: `apps/repos/views/webhooks.py` now uses typed webhook payload aliases, reducing `from typing import Any` usage in `apps/repos/` from 4 imports to 3.
- Replace remaining `Any` payloads with `TypedDict` shapes for GitHub CLI data as those modules become owned.

### Selected `apps/core/` modules

Small follow-ups:

- `apps/core/modeling/events.py`
- `apps/core/modeling/registry.py`

Keep broader `apps/core/system/ui/` rollout tracked separately until translation and uptime helpers are typed cleanly.

### Refresh procedure (on demand)

When maintainers need current backlog counts, run these exact patterns against the package under review:

- `TYPE_CHECKING`
- `from typing import Any`
- `(?::|->)\s*\b(list|dict|set|tuple)\b(?!\s*\[)` (requires `rg -nP` and shell quoting)

Use this workflow:

1. run the three searches for each target package (`rg -n` for the first two, `rg -nP '(?::|->)\s*\b(list|dict|set|tuple)\b(?!\s*\[)'` for the third)
2. count each result set for the current totals
3. record refreshed totals and the refresh date in the optional history section below, not in the evergreen checklist sections above

## Regression tracking during rollout

If a rollout candidate fails MyPy repeatedly across runs, keep it in the checklist as a tracked regression instead of silently removing it from scope.

Current tracked regressions:

- None currently tracked.

When you hit a persistent failure:

1. rerun the failing target
2. fix obvious local typing issues
3. record the remaining blocker here with the failing module path and reason

That keeps ownership monotonic while making the remaining debt explicit.

## CI signal review

The MyPy rollout is now enforced in `.github/workflows/mypy.yml` for the approved paths only.
Keep the signal useful after each rollout step:

1. review the uploaded `mypy.log` artifact and local pre-commit output
2. fix newly actionable annotations in the owned paths
3. cleave noisy or false-positive suppressions down to the narrowest override that still documents the real integration gap
4. leave excluded apps out of the blocking workflow until their checklist step is complete

If a warning turns out to be persistent noise, document the exact module and reason next to the corresponding override so later rollout work can remove it deliberately instead of normalizing broad ignores.

## Optional history (point-in-time snapshots)

Use this section for dated snapshots when a review or release needs an auditable before/after comparison. Keep entries date-stamped and append-only.

### Snapshot: 2026-03-21

`apps/protocols/`

- `TYPE_CHECKING`: 0
- `from typing import Any`: 0
- unparameterized collections: 6

`apps/repos/`

- `TYPE_CHECKING`: 8
- `from typing import Any`: 3
- unparameterized collections: 20

`apps/core/` selected modules

- `TYPE_CHECKING`: 0
- `from typing import Any`: 2
- unparameterized collections: 7
