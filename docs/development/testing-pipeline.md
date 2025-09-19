# Testing Pipeline

The CI workflow keeps the feedback cycle short by running a full test pass for
one baseline role and targeted smoke runs for any additional roles impacted by a
change. This document explains how the pipeline chooses those roles and what to
update when introducing architecture-specific features.

## Baseline coverage

* The `scripts/detect_impacted_roles.py` helper reads `node_roles.yml` to build a
  manifest of known node roles. The first entry in the manifest is treated as the
  baseline role. Currently, the baseline is `Terminal`.
* Every CI execution runs the complete test suite once for the baseline role.
  The job sets `NODE_ROLE=Terminal`, ensuring Django and Celery initialise with
  baseline settings.

## Targeted role smoke runs

* When changes match any manifest patterns for non-baseline roles, the detector
  adds those roles to the matrix as "targeted" entries. The `tests` job exports
  both `NODE_ROLE=<role>` and `NODE_ROLE_ONLY=1` for those matrix items.
* The pytest hook in `tests/conftest.py` watches for `NODE_ROLE_ONLY`. When the
  flag is present, only tests explicitly marked for the given role execute;
  unmarked tests are skipped so the job focuses on role-specific smoke checks.
* Mark a test with `@pytest.mark.role("Constellation")` (or another role name)
  when it validates functionality that only applies to that role. Tests can list
  multiple roles by applying the decorator more than once.

## Full matrix safety net

The workflow keeps a scheduled run (`0 2 * * *`) that forces the complete role
matrix. When the detector does not find any role-specific matches—such as during
that scheduled run—it emits every role from the manifest, so CI exercises the
entire matrix and catches regressions caused by cross-role interactions.

## Adding architecture-specific smoke coverage

When shipping a feature that only applies to a subset of roles:

1. Update `node_roles.yml` so the detector knows which file patterns correspond
   to the new logic. Group related patterns under the appropriate role name and
   keep the baseline role listed first.
2. Add or update tests that cover the role-specific behaviour and mark them with
   `@pytest.mark.role("RoleName")`.
3. If the role requires a quick smoke path (for example, spinning up a different
   Celery worker), ensure the relevant fixtures or utilities respect the
   `NODE_ROLE` environment variable so the targeted run exercises the feature.

Following these steps keeps the pipeline lean while still providing strong
coverage for architecture-specific functionality.
