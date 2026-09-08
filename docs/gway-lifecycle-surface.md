# GWAY lifecycle surface inventory

This document inventories and incrementally normalizes the Arthexis lifecycle surface introduced in #120 so deployment internals move behind direct GWAY lifecycle hooks.

## Target boundary

The public lifecycle interface should be GWAY itself (`gway install arthexis`, `gway upgrade arthexis`, and `gway service ...`). Arthexis should expose application capabilities through its Django adapter, while filesystem layout and deployment preparation remain internal implementation details.

GWAY should continue to own checkout/update and systemd mechanics. Arthexis should own application-specific preparation such as migrations, static collection, and future application validation.

## Public Django/GWAY surface

The deployment-oriented Django commands introduced in #120 have been removed:

- `managed_layout`
- `managed_prepare`

They are no longer part of the public `gway arthexis ...` command surface. The user-facing project path belongs to `gway path arthexis`; virtualenv, filesystem layout, and deployment preparation do not need Arthexis commands.

## Internal Python lifecycle surface

`apps/core/system/lifecycle.py` now exposes only the canonical application-oriented lifecycle API:

- `DEFAULT_INSTALL_ROOT`
- `InstallationLayout`
- `layout()`
- `prepare()`
- `install()`
- `upgrade()`
- `ensure_environment()`
- `run_python()`
- `install_project()`
- `run_manage()`
- `migrate()`
- `collectstatic()`
- `current_python()`

The temporary `ManagedLayout`, `managed_layout()`, `prepare_managed_install()`, and `DEFAULT_MANAGED_ROOT` compatibility aliases have been removed.

`ARTHEXIS_INSTALL_ROOT` is now the canonical environment override. `ARTHEXIS_MANAGED_ROOT` remains as a legacy fallback only, with the canonical variable taking precedence when both are present.

Git checkout/update and service management remain outside this module and continue to belong to GWAY.

## Manifest surface

`gway.toml` declares the target installation layout:

```toml
[install]
root = "/opt/arthexis"
checkout = "app"
environment = ".venv"
```

It declares direct Python lifecycle hooks:

```toml
[lifecycle]
install = "apps.core.system.lifecycle:install"
upgrade = "apps.core.system.lifecycle:upgrade"
```

Classification: **application-side contract established**.

The hook targets are importable and tested in Arthexis. GWAY core still needs to consume the `[install]` and `[lifecycle]` tables; until that lands, these entries are declarative application-side metadata.

## Compatibility bridge

`scripts/managed_lifecycle.py` has been removed after the repository audit found no independent callers. Lifecycle invocation now has one intended path: GWAY consumes the manifest and calls the canonical Python hooks.

## Tests

`apps/core/tests/test_managed_lifecycle.py` verifies:

- `/opt/arthexis` as the default root through `layout()`
- `ARTHEXIS_INSTALL_ROOT` as the canonical override
- `ARTHEXIS_MANAGED_ROOT` as a legacy fallback
- canonical override precedence over the legacy fallback
- preparation ordering through `prepare()`
- failure when the checkout is absent
- `install()` and `upgrade()` delegate to the common preparation sequence
- manifest lifecycle targets resolve to importable callables

## Cleanup sequence for this draft PR

- [x] Normalize the internal lifecycle API and remove unnecessary `managed_*` terminology from the canonical implementation.
- [x] Switch `gway.toml` from public Django lifecycle commands to direct Python hooks.
- [x] Migrate lifecycle tests to the canonical internal API and direct-hook contract.
- [x] Remove the public `managed_layout` and `managed_prepare` Django commands.
- [x] Retire `scripts/managed_lifecycle.py` and remove the temporary `managed_*` compatibility aliases.
- [ ] Add/validate the intended public Arthexis application surface (`node-role`, `version`, `status`) and finish the Arthexis-side declarative service/runtime ownership cleanup.
