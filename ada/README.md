# Ada App Layer (GNATCOLL + SQLite)

This directory uses a Django-like organization for Ada code while exposing a
full app matrix for product composition.

## Layout

- `src/`
  - `arthexis-orm.*`: shared SQLite persistence primitives.
  - `arthexis-apps.*`: central app installer registry.
- `apps/<app>/`
  - `models/`: schema owned by the app.
  - `views/`: read-projection SQL contracts.
  - `templates/`: AdaCore template identifiers.
  - `functions/`: SQL-callable functions and helper views.
  - `triggers/`: model invariants executed by SQLite.

## App matrix

Backbone apps:

- `app`: owns `App/App` registrations and optional app toggles.
- `model`: owns `Model/Model` registrations and optional model toggles.
- `product`: owns `Product/Product` entry points and app/model bindings.

Component apps:

- `functions`, `models`, `templates`, and `views` are themselves apps so each
  component kind is represented in the matrix and can be statically checked.

Optional apps:

- `test`: owns `Test/Test` toggles and can be enabled per product.

Domain apps:

- `ocpp`: charge points and transactions.

## Initial products

- `ocpp_charger_web`: OCPP charger with web-oriented app and view bindings.
- `ocpp_cli_simulator`: CLI OCPP simulator product profile.

This keeps behavior app-scoped, lets products declare exact app/model
participation, and preserves optional capabilities without restricting admin
power.
