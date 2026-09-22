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

apps/ocpp/protocol/v201/outbound/configuration.py
tests/apps/ocpp/protocol/v201/outbound/test_configuration.py

arthexis/reconciliation/importer.py
tests/arthexis/reconciliation/test_importer.py
```

The mirror is a package-level ownership rule, not a requirement to create one
test module for every source module. Closely related behavior may share a test
module when the production ownership is still clear.

Tests should not remain in a broad or historical feature bucket after the
production code has acquired a narrower package owner. Shared test helpers
belong at the narrowest common package that needs them.

## Explicit exceptions

Two top-level test areas are intentionally not source mirrors:

- `tests/integration/` contains behavior whose purpose is to verify contracts
  across multiple production packages. A test belongs here because crossing
  package boundaries is the behavior under test, not because its owner is
  unclear.
- `tests/deploy/` contains repository, recipe, workflow, deployment, and
  service-transition behavior that does not have a matching Python package.

Repository-wide architecture or configuration tests may remain outside an app
package only when no narrower source owner exists.

## Target topology

The test suite is being refactored toward this shape:

```text
tests/
  apps/
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
  arthexis/
    reconciliation/
    test_markdown_site.py
    test_ready.py
    test_server.py
  integration/
    ocpp/
  deploy/
```

This target intentionally retains the `apps/` level so the tests mirror the
actual source import tree rather than flattening Django apps directly beneath
`tests/`.

## Refactor rules

During the topology refactor:

1. Moves and splits should preserve existing test semantics before behavior is
   cleaned up or expanded.
2. A test should be assigned to the package that owns the behavior it proves,
   not merely the highest-level feature that uses that behavior.
3. Broad tests should be split when they span production packages with distinct
   responsibilities.
4. Cross-package scenarios should be named and retained as integration tests
   rather than disguised as unit tests.
5. Deployment and workflow assertions should remain separate from Python
   package tests.
6. Test helpers should be narrowed only after the test modules themselves have
   settled into their final packages.
7. Architectural enforcement should guard package topology without requiring
   artificial one-to-one source/test file parity.

## T0 baseline

The topology refactor starts from `main` commit
`eef9fb7b482bbe56f7941ac982b5c64c7673280d`.

At that baseline:

- the complete Django test suite ran 109 tests;
- 109 tests passed and 1 test was skipped;
- Python 3.10, 3.11, and 3.13 compatibility jobs passed;
- Quality, Package, Clean Install, Secret Scan, and Watchtower Deploy passed.

T0 establishes these rules and the baseline only. File moves begin in T1.
