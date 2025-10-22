# Pytest node role and feature markers

The CI pipeline now runs the full baseline test suite for every node role
(Watchtower, Control, Satellite, and Terminal). Role-specific and hardware
feature checks are enabled on top of that baseline by annotating tests with
pytest markers. This keeps the common smoke coverage consistent while still
allowing specialised suites to run only where they make sense.

## Role markers

Use `@pytest.mark.role(<role name>)` when a test only applies to a particular
node role. Tests without a role marker are assumed to be part of the baseline
and run for every role.

Examples of role markers include:

- Terminal user-interface helpers.
- Control-surface tooling that never ships to other roles.
- Landing page fixtures scoped to a single role.

Multiple markers may be stacked on the same test (or declared via `pytestmark`)
when the behaviour spans several roles.

## Feature markers

When a test exercises functionality that depends on a `nodes.NodeFeature`, add
`@pytest.mark.feature(<feature slug>)`. The slug must match the entry from the
NodeFeature fixtures (for example `lcd-screen`, `rfid-scanner`, or
`celery-queue`). Feature-marked tests are only executed for roles that ship that
feature.

Use feature markers for suites that verify optional hardware integrations or
node capabilities. This keeps the per-role jobs focused on the features they
actually ship.

## Local filtering

Pytest honours the following environment variables:

- `NODE_ROLE` – skips tests whose role markers do not include the requested
  role. Baseline tests (without role markers) always run.
- `NODE_ROLE_ONLY` – when truthy, skips any test that lacks a role marker after
  applying the per-role filtering. This is useful for smoke runs that should
  cover only the role-specific suites and fail fast when markers are missing.
- `NODE_FEATURES` – comma-separated list of feature slugs to enable. Tests
  marked with `@pytest.mark.feature` are skipped if their features are not
  listed. Leaving this unset (the default) runs all feature tests.

Example invocations:

```bash
NODE_ROLE=Control pytest tests
NODE_ROLE=Terminal NODE_FEATURES="lcd-screen,gui-toast" pytest tests/test_lcd_*.py
```

Leaving `NODE_FEATURES` unset replicates the behaviour of the default CI jobs,
which automatically populate the variable based on the role's enabled features.
