# Game app removal and legacy upgrade path

The `apps.game` package has been removed from the fresh-install baseline.
New environments should **not** include `apps.game` in manifest-driven app loading or `INSTALLED_APPS` discovery.

## Scope of this cleanup

This repository state assumes the cleanup is applied as part of a fresh-install baseline or migration reset process.
The deleted package included only `apps/game/migrations/0001_initial.py`, and no other app declared inbound migration dependencies on `game.0001_initial` in the current tree.

## Legacy upgrade path for existing installations

Existing installations that already applied `apps.game` migrations must follow a legacy branch or release path before moving onto this baseline:

1. Start from a branch or release where `apps.game` still exists in shipped history.
2. Add and apply a dedicated decommission migration/release step there to archive or drop the `game_avatar` table as needed for that environment.
3. Only after that decommission step is applied everywhere should the installation move to a baseline where `apps.game` is absent from the repository.

Until such a legacy decommission path exists, do not treat this cleanup as an in-place upgrade for environments that already migrated `apps.game`.
