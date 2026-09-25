# Preserved reconciliation fixtures

This directory contains accepted, repository-safe historical Arthexis capture bundles used to exercise cross-major reconciliation against real field-shaped data.

## Acceptance policy

An accepted fixture must:

- be produced from a finalized capture through `scripts/reconcile.py preserve`;
- verify with the normal capture verifier;
- contain preservation provenance in its manifest;
- contain no exported legacy metadata files, source paths, or configuration fingerprints;
- preserve the relational structure needed by reconciliation while pseudonymizing customer/charger/card identities;
- fail preservation when populated secret-bearing columns are not explicitly handled;
- be treated as immutable after acceptance.

Do not commit raw/private field captures here.

## Adding a fixture

1. Capture and verify the private field source.
2. Run `python scripts/reconcile.py preserve <capture> --output tests/fixtures/reconciliation`.
3. Review the preserved database and manifest for repository safety.
4. Commit the finalized preserved capture directory.
5. CI will verify every accepted preserved capture in this corpus.

Changing an accepted fixture should normally be done by adding a new preserved capture rather than mutating the existing one.
