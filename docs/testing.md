# Test Architecture

Arthexis tests mirror the production package tree so ownership stays obvious as
the source is refactored.

## Ownership rule

Tests for first-party Python packages belong under the corresponding import
path beneath `tests/`.

Examples:

```text
apps/ocpp/protocol/frames.py
tests/apps/ocpp/protocol/test_frames.py

apps/ocpp/protocol/v201/outbound/
tests/apps/ocpp/protocol/v201/outbound/

arthexis/reconciliation/importer.py
tests/arthexis/reconciliation/test_importer.py
```

The mirror is a package-level ownership rule, not a requirement to create one
test module for every source module. Closely related behavior may share a test
module when the production ownership is still clear.

A source-owned test should not remain in a broad or historical feature bucket
after the production code has acquired a narrower package owner.

## Final topology

The maintained test tree has four kinds of ownership:

```text
tests/
  apps/                 # mirrors apps.*
    base/
    cards/
    celery/
    energy/
    events/
    nodes/
    ocpp/
      admin/
      domain/
      management/
        charger/
        commands/
      models/
      protocol/
        v16/
          inbound/
          outbound/
        v201/
          inbound/
          outbound/
      services/
      simulator/
      transport/
    sigils/
  arthexis/             # mirrors arthexis.*
    reconciliation/
  integration/          # deliberate cross-package/runtime scenarios
    ocpp/
  deploy/               # deployment/workflow/recipe behavior
  ocpp/                 # external OCPP conformance/spec tooling
  test_architecture.py  # repository-wide topology contract
  test_readme.py        # repository-wide public README contract
```

The `apps/` level is intentional: tests mirror the actual production import
tree rather than flattening Django apps beneath `tests/`.

## Explicit exceptions

- `tests/integration/` verifies behavior whose purpose is to cross production
  package boundaries or exercise a real runtime boundary. It is not a fallback
  for tests whose owner is unclear.
- `tests/deploy/` verifies repository, recipe, workflow, deployment, and
  service-transition behavior that has no matching Python package.
- `tests/ocpp/` owns external OCPP schema/conformance and spec-refresh
  tooling. Protocol implementation tests belong under
  `tests/apps/ocpp/protocol/`; the conformance bucket must not recreate
  parallel `v16/` or `v201/` implementation package trees.
- Root `test_*.py` modules are reserved for repository-wide contracts. The
  architecture test enforces the current allowlist.

## Test helpers

Helpers live at the narrowest common ancestor that uses them.

- `tests/apps/ocpp/builders.py` contains OCPP model builders shared across
  several OCPP source-package tests and OCPP integration scenarios.
- `tests/apps/ocpp/fakes.py` contains OCPP test doubles shared across
  protocol/transport tests.
- `tests/integration/ocpp/support.py` contains helpers used only by OCPP
  integration flows.
- `tests/ocpp/support.py` contains helpers used only by the external
  conformance suite.

Do not promote a helper toward the repository root merely for convenience.
Move it outward only when multiple sibling ownership areas genuinely share it.

## Architecture enforcement

`tests/test_architecture.py` protects the package topology without imposing
one-test-file-per-source-file parity. It verifies that:

1. every first-party app package under `apps/*/` has a `tests/apps/<app>/` package;
2. mirrored test packages under `tests/apps/` and `tests/arthexis/` point
   to real source packages;
3. only explicitly approved repository-wide tests live at the test root;
4. the integration, deploy, and conformance exception buckets remain explicit Python test packages;
5. the conformance suite does not recreate legacy version-package mirrors.

When adding a new first-party app, create its mirrored test package as part of
the same change. When moving production packages, move their source-owned tests
with them.

## Refactor principles

1. Assign a test to the package that owns the behavior it proves, not merely
   the highest-level feature that uses it.
2. Split broad tests when they span production packages with distinct
   responsibilities.
3. Name genuine cross-package scenarios as integration tests.
4. Keep deployment/workflow assertions separate from Python package tests.
5. Preserve semantics during structural moves; change behavior separately.
6. Keep helpers at the narrowest useful common ancestor.
7. Enforce package topology, not artificial file parity.

## Pytest conventions

Pytest is the native test runner and preferred test style. New or refactored
tests should use plain test functions, plain `assert` expressions, and pytest
fixtures rather than adding new `django.test.TestCase` wrappers solely for
database setup or assertion helpers.

For modules whose tests all require database access, declare
`pytestmark = pytest.mark.django_db` once at module scope. Use narrower marks
when only some tests need the database. Keep fixtures local to the test module
until multiple sibling modules genuinely share the same setup; then move the
fixture only to their narrowest common `conftest.py`.

Use `pytest.raises` for expected exceptions and fixtures for repeated setup
that represents a reusable test input. Do not introduce fixtures merely to
hide one-off object construction: explicit setup is preferable when it makes
the behavior under test easier to read.

The pytest configuration runs with strict config and strict marker validation
so misspelled or undeclared test configuration fails early.

## Test-quality guidance

The topology is stable enough for feature development. Future test work should
improve behavior coverage incrementally rather than starting another broad
structural cleanup.

Some overlap is deliberate. OCPP registry, version-matrix, support-matrix,
schema-conformance, and integration tests exercise different contracts even
when they touch the same actions. Keep overlapping coverage when each layer
would catch a meaningfully different regression. Avoid adding another copy of
the same assertion merely because a higher-level scenario reaches the same
code.

Prefer public test seams over implementation details. In particular, new tests
should not depend on private registries such as `_handlers` when a public
introspection surface can express the contract. Existing private-internal
assertions may be migrated when the relevant production API is touched rather
than forcing an unrelated cleanup.

Integration tests should prove that boundaries compose correctly, not repeat
the exhaustive behavior already owned by package tests. Keep a representative
end-to-end path for important workflows, while detailed action, validation,
persistence, and error cases remain with their source-package tests.

OCPP outbound recovery follows this rule explicitly:
`tests/integration/ocpp/test_outbound_recovery_flow.py` exercises the fresh
WebSocket reconnect boundary and durable SQL outcomes, while detailed policy,
state-transition, and race assertions remain under
`tests/apps/ocpp/transport/` and `tests/apps/ocpp/protocol/`.

Conformance tests should turn external metadata into assertions whenever
practical. If a conformance workflow reads an official index or manifest, use
that data to verify completeness rather than loading it without checking it.

Coverage depth may differ by app because complexity differs. Small apps do not
need artificial tests just to match OCPP's volume, but whenever `base`,
`cards`, `celery`, `energy`, `events`, `nodes`, `sigils`, or another
app gains substantive behavior, add direct source-owned tests with the feature
instead of relying on incidental integration coverage.

When reviewing new tests, ask:

1. What production package owns the behavior?
2. Is this assertion already proved at the same layer?
3. Does an integration test verify composition rather than re-test internals?
4. Is the test coupled to a private implementation detail unnecessarily?
5. Does new application behavior have direct tests at its source owner?

## Refactor history

The topology refactor began from `main` commit
`eef9fb7b482bbe56f7941ac982b5c64c7673280d`, where the complete Django test
suite ran 109 tests (one skipped) and the supported Python compatibility,
Quality, Package, Clean Install, Secret Scan, and Watchtower Deploy checks were
green.

Sprints T1-T7 established the mirrored package tree and explicit integration
layer. T8 narrowed helpers, T9 added architecture enforcement, and T10 finalized
this document and the resulting topology.
