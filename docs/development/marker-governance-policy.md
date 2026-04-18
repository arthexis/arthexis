# Test marker governance policy

This page defines how Arthexis maintains test markers over time.

## Purpose

Markers keep suite feedback loops fast and trustworthy by separating must-pass gates from broader integration coverage.

Use marker updates to keep critical paths protected while allowing slower checks to run in the right pipeline tier.

## Governance rules

- Treat `critical` as install/upgrade and safety-sensitive coverage.
- Use `integration` for slower, broader behavior checks that remain important but do not need to block every fast gate.
- Record marker promotions/demotions in dated entries within `docs/development/testing/test-suite-notes.md`.
- Keep ledger entries append-only historical records; do not rewrite past decisions unless correcting factual errors.
- When demoting tests, ensure representative coverage for the same behavior remains in a fast path.

## Historical ledgers

Historical marker-change decisions are kept in the testing notes document:

- [`docs/development/testing/test-suite-notes.md`](testing/test-suite-notes.md)
