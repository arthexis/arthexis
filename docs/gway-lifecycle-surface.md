# GWAY lifecycle surface inventory

This document inventories and incrementally normalizes the Arthexis lifecycle surface introduced in #120 so deployment internals move behind direct GWAY lifecycle hooks.

## Target boundary

The public lifecycle interface is GWAY itself (`gway install arthexis`, `gway upgrade arthexis`, and `gway service ...`). Arthexis exposes application capabilities through its Django adapter, while filesystem layout and deployment preparation remain internal implementation details.

GWAY owns checkout/update and systemd mechanics. Arthexis owns application-specific preparation, role/profile intent, migrations, static collection, and application health.

## Public Django/GWAY surface

The deployment-oriented Django commands introduced in #120 have been removed:

- `managed_layout`
- `managed_prepare`

They are no longer part of the public `gway arthexis ...` command surface. The user-facing project path belongs to `gway path arthexis`; virtualenv, filesystem layout, and deployment preparation do not need Arthexis commands.

The intended application-facing surface is now present:

- `node_role` / `gway arthexis node-role` reports the normalized `settings.NODE_ROLE` value.
- `version` / `gway arthexis version` reports the checkout `VERSION` file.
- `status` / `gway arthexis status` reports only `GOOD` or `FAIL` and exits non-zero on failure.

`status` intentionally checks Arthexis-owned essentials only: a supported node role, database reachability, and whether migrations are fully applied. It does not duplicate systemd, network, host, RFID, camera, or other package-owned telemetry.

## Internal Python lifecycle surface

`apps/core/system/lifecycle.py` exposes only the canonical application-oriented lifecycle API:

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

`ARTHEXIS_INSTALL_ROOT` is the canonical environment override. `ARTHEXIS_MANAGED_ROOT` remains as a legacy fallback only, with the canonical variable taking precedence when both are present.

Git checkout/update and service management remain outside this module and belong to GWAY.

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

## Service and runtime ownership

Arthexis keeps ownership of service topology and role/profile decisions. The existing role model (`Control`, `Satellite`, `Terminal`, `Watchtower`) and application-profile gates remain the source of application intent.

For a GWAY-managed installation, Arthexis must not be the authority for rendering, creating, enabling, disabling, starting, stopping, or restarting systemd units. Existing shell/systemd logic remains legacy compatibility code for repository installs only and must not be called by the canonical GWAY-managed lifecycle path.

The final role-aware service declarations themselves depend on generic GWAY support for multi-service/role-aware manifests. They should be encoded there once that generic schema exists rather than inventing an Arthexis-only manifest dialect in this repository.

Mutable runtime data must likewise remain outside the replaceable managed checkout as the GWAY `/opt` adoption work lands. GWAY owns the managed checkout/environment layout; Arthexis owns the meaning of its application data and configuration.

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

`apps/core/tests/test_system_identity.py` verifies node-role normalization, version lookup, healthy/failing status behavior, and the public command wrappers.

## Cleanup sequence for this PR

- [x] Normalize the internal lifecycle API and remove unnecessary `managed_*` terminology from the canonical implementation.
- [x] Switch `gway.toml` from public Django lifecycle commands to direct Python hooks.
- [x] Migrate lifecycle tests to the canonical internal API and direct-hook contract.
- [x] Remove the public `managed_layout` and `managed_prepare` Django commands.
- [x] Retire `scripts/managed_lifecycle.py` and remove the temporary `managed_*` compatibility aliases.
- [x] Add/validate the intended public Arthexis application surface (`node-role`, `version`, `status`) and record the service/runtime ownership boundary for the GWAY-managed path.
