# Pytest node role and feature markers

Our current CI runs a single Python test pipeline (on both a fresh install and
an upgraded install) without a role matrix. Every CI job invokes `pytest` with no
`NODE_ROLE` or `NODE_FEATURES` environment variables set, so the entire suite
runs in the default configuration. There are no per-role test partitions at the
moment.

## Role markers

Use `@pytest.mark.role(<role name>)` when a test only applies to a particular
node role. Tests without a role marker are assumed to be part of the baseline
and run everywhere, which matches the default CI behaviour.

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
`celery-queue`). Feature-marked tests should be used for suites that verify
optional hardware integrations or node capabilities.

Because CI leaves `NODE_FEATURES` unset, feature-marked tests always run in the
default pipeline. Locally you can constrain them to a specific set of features
by exporting `NODE_FEATURES`.

## Local filtering

Pytest honours the following environment variables for local filtering:

- `NODE_ROLE` – skips tests whose role markers do not include the requested
  role. Baseline tests (without role markers) always run.
- `NODE_ROLE_ONLY` – when truthy, skips any test that lacks a role marker after
  applying the per-role filtering. This is useful for smoke runs that should
  cover only the role-specific suites and fail fast when markers are missing.
- `NODE_FEATURES` – comma-separated list of feature slugs to enable. Tests
  marked with `@pytest.mark.feature` are skipped if their features are not
  listed. Leaving this unset (the default in CI) runs all feature tests.

Example invocations:

```bash
NODE_ROLE=Control pytest tests
NODE_ROLE=Terminal NODE_FEATURES="lcd-screen,gui-toast" pytest tests/test_lcd_*.py
```
