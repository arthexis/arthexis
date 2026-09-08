# GWAY lifecycle surface inventory

This document inventories the Arthexis lifecycle surface introduced in #120 before deployment internals are moved behind direct GWAY lifecycle hooks. This first step intentionally changes no runtime behavior.

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

`apps/core/system/lifecycle.py` currently defines:

- `DEFAULT_MANAGED_ROOT`
- `DEFAULT_CHECKOUT_NAME`
- `DEFAULT_ENVIRONMENT_NAME`
- `ManagedLayout`
- `managed_layout()`
- `ensure_environment()`
- `run_python()`
- `install_project()`
- `run_manage()`
- `migrate()`
- `collectstatic()`
- `prepare_managed_install()`
- `current_python()`

Classification: **retain the capability, normalize the terminology and entry points**.

The intended internal direction is application-oriented names such as `InstallationLayout`, `layout()`, `install()`, and `upgrade()`. Lower-level helpers can remain private or narrowly public where independently useful. Git checkout/update and service management must not move into this module.

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

`apps/core/tests/test_managed_lifecycle.py` currently verifies:

- `/opt/arthexis` as the default root
- the `ARTHEXIS_MANAGED_ROOT` override
- canonical preparation ordering: venv, package install, migrate, collectstatic
- failure when the checkout is absent

Classification: **preserve the behavior, migrate the naming and contract**.

The tests should move toward the internal lifecycle API and direct manifest hook contract. Tests should not require `managed_layout` or `managed_prepare` to remain public Django commands.

## Reference audit

The six lifecycle-related files introduced by #120 are:

- `apps/core/management/commands/managed_layout.py`
- `apps/core/management/commands/managed_prepare.py`
- `apps/core/system/lifecycle.py`
- `apps/core/tests/test_managed_lifecycle.py`
- `gway.toml`
- `scripts/managed_lifecycle.py`

The two public commands import the lifecycle module directly, and the compatibility bridge imports the same two `managed_*` entry points. No additional lifecycle file was introduced by #120. A fresh code-search index did not return additional references, so subsequent cleanup commits should still perform an exact repository-wide reference check before deleting or renaming symbols.

## Cleanup sequence for this draft PR

- [ ] Normalize the internal lifecycle API and remove unnecessary `managed_*` terminology.
- [ ] Switch `gway.toml` from public Django lifecycle commands to direct Python hooks once the GWAY hook contract is defined.
- [ ] Migrate lifecycle tests to the internal/direct-hook contract.
- [ ] Remove the public `managed_layout` and `managed_prepare` Django commands.
- [ ] Retire or narrow `scripts/managed_lifecycle.py` after legacy admin callers no longer require it.
- [ ] Validate that the public `gway arthexis ...` surface contains application capabilities rather than deployment internals.

These items are intentionally unchecked: this inventory commit is documentation only.