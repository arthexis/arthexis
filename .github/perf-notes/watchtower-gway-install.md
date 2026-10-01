# Watchtower canonical Gway install performance

Baseline from manual Watchtower run `36792718479` on 2026-09-30:

- `Install exact canonical Gway`: approximately 24.6 seconds.
- Previous implementation created two virtual environments and installed the same exact Gway SHA twice.

This branch changes the deployment to prepare and verify a single `/var/lib/gway/venv.next` candidate, validate its `direct_url.json` Git commit ID against `GWAY_EXPECTED_SHA`, then atomically swap it into `/var/lib/gway/venv` while retaining `/var/lib/gway/venv.previous` rollback.

The workflow emits `watchtower_timing` records for candidate venv creation, pip setup, Gway install, pre-swap verification, runtime swap, and post-swap verification so the next Watchtower deployment can establish the new measured baseline.
