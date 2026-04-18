# Test marker governance policy

This page defines how Arthexis maintains test markers over time.

## Purpose

Markers keep suite feedback loops fast and trustworthy by separating routine checks from broader integration coverage.

## Governance rules

- Use `integration` for slower, broader behavior checks that remain important but do not need to block every fast gate.
- Use `slow` when runtime cost is the primary reason a test should run in expanded schedules instead of every fast gate.
- Record marker promotions/demotions in dated entries within `docs/development/testing/test-suite-notes.md`.
- Keep ledger entries append-only historical records; do not rewrite past decisions unless correcting factual errors.
- When changing marker usage, ensure representative coverage for the same behavior remains in the default PR-safe path.

## Historical ledgers

Historical marker-change decisions are kept in the testing notes document:

- [`docs/development/testing/test-suite-notes.md`](testing/test-suite-notes.md)
