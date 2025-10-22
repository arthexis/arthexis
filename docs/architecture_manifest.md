# Node test matrix

The architecture manifest has been retired. The CI pipeline now determines its
node test plan dynamically by reading the `nodes.NodeRole` and
`nodes.NodeFeature` fixtures. This information is used by
`scripts/build_node_ci_plan.py` to publish two values:

- The per-role test matrix (which combinations of `NODE_ROLE` and
  `NODE_FEATURES` the workflow should run).
- Whether the change touched the database schema. The `check-migrations` job is
  skipped automatically when neither models nor migrations changed.

Each matrix entry runs the full baseline test suite plus any feature-specific
checks that were annotated with `@pytest.mark.feature(<feature slug>)`. This
keeps the per-role jobs concise without sacrificing coverage.

## Adding new feature coverage

1. Ensure the relevant `nodes.NodeFeature` fixture lists every role that ships
   the feature. The CI plan automatically reads these fixtures.
2. Annotate the tests that exercise the feature with
   `@pytest.mark.feature(<slug>)`. Refer to
   `docs/development/pytest-role-markers.md` for guidance on naming and
   filtering.
3. Run the tests locally with `NODE_ROLE` and `NODE_FEATURES` set to the target
   role to confirm the markers work as expected.

With this simplified approach most changes automatically exercise all roles,
and only feature-specific suites need explicit annotation.

## Current feature suites

- `celery-queue` – `tests/test_celery_no_debug.py` verifies the worker starts
  without debug logging and `tests/test_auto_upgrade_scheduler.py` ensures the
  periodic upgrade task is registered against Celery.
