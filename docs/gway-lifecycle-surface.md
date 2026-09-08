# GWAY lifecycle surface inventory

This document inventories the Arthexis lifecycle surface introduced in #120 before deployment internals are moved behind direct GWAY lifecycle hooks. The draft PR is being implemented incrementally so compatibility can be preserved between chunks.

## Target boundary

The public lifecycle interface should be GWAY itself (`gway install arthexis`, `gway upgrade arthexis`, and `gway service ...`). Arthexis should expose application capabilities through its Django adapter, while filesystem layout and deployment preparation remain internal implementation details.

GWAY should continue to own checkout/update and systemd mechanics. Arthexis should own application-specific preparation such as its Python environment contents, migrations, static collection, and future application validation.

## Current public Django/GWAY surface

### `apps/core/management/commands/managed_layout.py`

Exposes `managed_layout()` as the public Django command `managed_layout`, including the installation root, checkout, environment, and Python interpreter paths.

Classification: **remove from the public Django/GWAY surface**.

Replacement: retain an internal layout API for lifecycle code. The user-facing project path belongs to `gway path arthexis`; virtualenv and internal filesystem details do not require an Arthexis command.

### `apps/core/management/commands/managed_prepare.py`

Exposes `prepare_managed_install()` as the public Django command `managed_prepare`, with flags for root selection, editable installation, migrations, and static collection.

Classification: **remove from the public Django/GWAY surface**.

Replacement: GWAY should invoke an importable Arthexis lifecycle function directly from the manifest rather than dispatching a Django command whose purpose is deployment orchestration.

## Internal Python lifecycle surface

`apps/core/system/lifecycle.py` now has canonical application-oriented names:

- `DEFAULT_INSTALL_ROOT`
- `InstallationLayout`
- `layout()`
- `prepare()`
- `ensure_environment()`
- `run_python()`
- `install_project()`
- `run_manage()`
- `migrate()`
- `collectstatic()`
- `current_python()`

Compatibility aliases remain temporarily for callers introduced by #120:

- `DEFAULT_MANAGED_ROOT`
- `ManagedLayout`
- `managed_layout()`
- `prepare_managed_install()`

Classification: **canonicalized internally; compatibility aliases remain until later cleanup chunks**.

Git checkout/update and service management remain outside this module and continue to belong to GWAY.

## Manifest surface

`gway.toml` currently declares the target installation layout:

```toml
[install]
root = "/opt/arthexis"
checkout = "app"
environment = ".venv"
```

This is the desired deployment contract and should remain declarative.

The current lifecycle table leaks Django command names:

```toml
[lifecycle]
layout = "managed_layout"
prepare = "managed_prepare"
```

Classification: **replace**.

Target direction: lifecycle entries should reference importable Python callables directly, for example:

```toml
[lifecycle]
install = "apps.core.system.lifecycle:install"
upgrade = "apps.core.system.lifecycle:upgrade"
```

The exact hook schema must be implemented and validated in GWAY before it becomes authoritative.

## Compatibility bridge

`scripts/managed_lifecycle.py` is a shell-friendly wrapper around `managed_layout()` and `prepare_managed_install()` with `layout` and `prepare` subcommands.

Classification: **temporary compatibility surface**.

It can remain while legacy repository-install administration scripts are migrated, but it should not become the stable GWAY interface. Once callers use the canonical Python/GWAY lifecycle path, this bridge should be renamed, reduced, or removed.

## Tests

`apps/core/tests/test_managed_lifecycle.py` now verifies the canonical lifecycle API:

- `/opt/arthexis` as the default root through `layout()`
- the `ARTHEXIS_MANAGED_ROOT` override
- canonical preparation ordering through `prepare()`
- failure when the checkout is absent
- compatibility aliases remain available during migration

Classification: **behavior preserved; tests now target canonical internal names**.

The next test migration should cover the direct manifest hook contract. Tests should not require `managed_layout` or `managed_prepare` to remain public Django commands.

## Reference audit

The six lifecycle-related files introduced by #120 are:

- `apps/core/management/commands/managed_layout.py`
- `apps/core/management/commands/managed_prepare.py`
- `apps/core/system/lifecycle.py`
- `apps/core/tests/test_managed_lifecycle.py`
- `gway.toml`
- `scripts/managed_lifecycle.py`

The two public commands and the compatibility bridge still use the old compatibility names. That is intentional in chunk 2 so no public or shell behavior changes yet.

## Cleanup sequence for this draft PR

- [x] Normalize the internal lifecycle API and remove unnecessary `managed_*` terminology from the canonical implementation.
- [ ] Switch `gway.toml` from public Django lifecycle commands to direct Python hooks once the GWAY hook contract is defined.
- [x] Migrate lifecycle tests to the canonical internal API while preserving compatibility coverage.
- [ ] Remove the public `managed_layout` and `managed_prepare` Django commands.
- [ ] Retire or narrow `scripts/managed_lifecycle.py` after legacy admin callers no longer require it.
- [ ] Validate that the public `gway arthexis ...` surface contains application capabilities rather than deployment internals.
