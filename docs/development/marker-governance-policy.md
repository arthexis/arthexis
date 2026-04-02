# Test marker governance policy

This page defines how Arthexis maintains test markers over time.

## Purpose

Markers keep suite feedback loops fast and trustworthy by separating must-pass gates from broader integration coverage.

Use marker updates to keep critical paths protected while allowing slower checks to run in the right pipeline tier.

## Governance rules

- Treat `critical` as install/upgrade and safety-sensitive coverage.
- Use `integration` for slower, broader behavior checks that remain important but do not need to block every fast gate.
- Record marker promotions/demotions in a dated ledger file under `docs/non-canonical/archive/testing/`.
- Keep ledgers append-only historical records; do not rewrite past decisions unless correcting factual errors.
- When demoting tests, ensure representative coverage for the same behavior remains in a fast path.

## Historical ledgers

Historical marker-change ledgers live in:

- [`docs/non-canonical/archive/testing/`](../non-canonical/archive/testing/index.md)

The previous 2026-03-01 critical demotion ledger has been archived there.
