# Ada App Layer (GNATCOLL + SQLite)

This directory introduces a Django-like app organization for Ada code.

## Layout

- `src/`
  - `arthexis-orm.*`: shared SQLite persistence primitives.
  - `arthexis-apps.*`: central app installer registry.
- `apps/<app>/`
  - `models/`: schema owned by the app.
  - `views/`: read-projection SQL contracts.
  - `templates/`: AdaCore template identifiers.
  - `functions/`: SQL-callable functions/views.
  - `triggers/`: model invariants executed by SQLite.

## Current apps

- `core`: shared registry primitives and foundational trigger/function hooks.
- `ocpp`: OCPP charge points and transactions, including views/functions/triggers.

This keeps Ada behavior app-scoped, so no Ada model logic needs to live outside
`ada/apps/<app>/...`.
