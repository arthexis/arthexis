# GWAY lifecycle surface inventory

This document inventories and incrementally normalizes the Arthexis lifecycle surface introduced in #120 so deployment internals move behind direct GWAY lifecycle hooks.

## Target boundary

The public lifecycle interface should be GWAY itself (`gway install arthexis`, `gway upgrade arthexis`, and `gway service ...`). Arthexis should expose application capabilities through its Django adapter, while filesystem layout and deployment preparation remain internal implementation details.

GWAY should continue to own checkout/update and systemd mechanics. Arthexis should own application-specific preparation such as its Python environment contents, migrations, static collection, and future application validation.

## Public Django/GWAY surface

The deployment-oriented Django commands introduced in #120 have now been removed:

- `managed_layout`
- `managed_prepare`

They are no longer part of the public `gway arthexis ...` command surface. The user-facing project path belongs to `gway path arthexis`; virtualenv, filesystem layout, and deployment preparation do not need Arthexis commands.

## Internal Python lifecycle surface

`apps/core/system/lifecycle.py` now has canonical application-oriented names:

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

`install()` and `upgrade()` are semantic GWAY hook entry points. They intentionally share the same preparation sequence today while leaving room for install- and upgrade-specific behavior later.

Compatibility aliases remain temporarily for the compatibility bridge and any callers introduced by #120:

- `DEFAULT_MANAGED_ROOT`
- `ManagedLayout`
- `managed_layout()`
- `prepare_managed_install()`

Classification: **canonicalized internally; compatibility aliases remain until the bridge cleanup chunk**.

Git checkout/update and service management remain outside this module and continue to belong to GWAY.

## Manifest surface

`gway.toml` declares the target installation layout:

```toml
[install]
root = "/opt/arthexis"
checkout = "app"
environment = ".venv"
```

It declares direct Python lifecycle hooks rather than Django command names:

```toml
[lifecycle]
install = "apps.core.system.lifecycle:install"
upgrade = "apps.core.system.lifecycle:upgrade"
```

Classification: **application-side contract established**.

The hook targets are importable and tested in Arthexis. GWAY core still needs to learn how to consume the `[install]` and `[lifecycle]` tables; until that lands, these entries are declarative/forward-compatible and do not change GWAY runtime behavior by themselves.

## Compatibility bridge

`scripts/managed_lifecycle.py` remains as a shell-friendly wrapper around the compatibility aliases.

Classification: **temporary compatibility surface**.

It can remain while legacy repository-install administration scripts are checked and migrated, but it is not part of the stable GWAY interface. Once callers use the canonical Python/GWAY lifecycle path, the bridge and old aliases can be removed together.

## Tests

`apps/core/tests/test_managed_lifecycle.py` verifies the canonical lifecycle API and manifest contract:

- `/opt/arthexis` as the default root through `layout()`
- the `ARTHEXIS_MANAGED_ROOT` override
- canonical preparation ordering through `prepare()`
- failure when the checkout is absent
- `install()` and `upgrade()` delegate to the common preparation sequence
- manifest lifecycle targets resolve to importable callables
- compatibility aliases remain available during migration

Tests do not require `managed_layout` or `managed_prepare` to remain public Django commands.

## Cleanup sequence for this draft PR

- [x] Normalize the internal lifecycle API and remove unnecessary `managed_*` terminology from the canonical implementation.
- [x] Switch `gway.toml` from public Django lifecycle commands to direct Python hooks.
- [x] Migrate lifecycle tests to the canonical internal API and direct-hook contract while preserving compatibility coverage.
- [x] Remove the public `managed_layout` and `managed_prepare` Django commands.
- [ ] Retire `scripts/managed_lifecycle.py` and remove the temporary `managed_*` compatibility aliases after checking legacy callers.
- [ ] Add/validate the intended public Arthexis application surface (`node-role`, `version`, `status`) and then finish the Arthexis-side declarative service/runtime ownership cleanup.
