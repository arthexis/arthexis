# Test marker governance policy

This page defines how Arthexis maintains test markers over time.

## Purpose

Markers keep suite feedback loops fast and trustworthy when a behavior requires explicit selection semantics.

## Governance rules

- Keep marker usage minimal and only for fixture/setup semantics that are not about excluding broad test tiers.
- Record marker governance decisions in dated entries within `docs/development/testing/test-suite-notes.md`.
- Keep ledger entries append-only historical records; do not rewrite past decisions unless correcting factual errors.
- When changing marker usage, ensure representative coverage for the same behavior remains in the default PR-safe path.

## Historical ledgers

Historical marker-change decisions are kept in the testing notes document:

- [`docs/development/testing/test-suite-notes.md`](testing/test-suite-notes.md)
