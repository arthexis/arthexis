# Install, Start, Stop, Upgrade, Uninstall (Cookbook)

> **Single source of truth:** Operational script flags and lifecycle behavior are maintained in `docs/development/install-lifecycle-scripts-manual.md`. Update that manual first, then keep this cookbook as a short role-oriented entry point.

This cookbook is for operators choosing the right flow by node role. For exact flags, aliases, and parser-backed behavior, always use the canonical manual:

- [`docs/development/install-lifecycle-scripts-manual.md`](../../../docs/development/install-lifecycle-scripts-manual.md)

## Quick role-oriented flow

- **Terminal role:** install, start, stop, and upgrade locally with minimal runtime dependencies.
- **Satellite role:** install with Redis-backed runtime assumptions, then use start/stop and upgrade policy that matches your operations model.
- **Control role:** install with Control-specific runtime toggles (for example LCD or accessory services when enabled), then use stop/upgrade windows appropriate for attached hardware.
- **Watchtower role:** install for hosted/public-facing deployments, then use upgrade and reconfiguration flows from the canonical manual.

## Recommended operator sequence

1. Run installation (`install.sh` or Windows equivalent where applicable).
2. Start services (`start.sh`) and validate runtime health.
3. Stop services cleanly when needed (`stop.sh`).
4. Apply upgrades (`upgrade.sh`) using the channel and safety flags documented in the canonical manual.
5. Reconfigure toggles safely (`configure.sh`) when role/features change.
6. Uninstall only when intentionally retiring the node (`uninstall.sh`).

For all flag-level details, do not duplicate tables here; keep them in the canonical manual.
