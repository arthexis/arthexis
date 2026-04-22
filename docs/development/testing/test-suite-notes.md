# Test Suite Notes

This document tracks focused test-suite decisions that are too specific for `tests/README.md` but still useful for maintainers.

## Current notes

- The env refresh integration test was retired because CI and local workflows already run environment refresh before test execution, so failures now surface earlier.
- Document rendering integration coverage moved to higher-level doc generation checks and manual QA for end-to-end output, while unit coverage retains HTML escaping checks for plain text.
- 2026-04-18: Removed `critical` marker usage and the PR marker-only workflow so both PR validation and `install-hourly.yml` now run the full default pytest suite without marker exclusions.

## Marker change ledger

- 2026-04-16: Demoted five `apps.repos` tests from `critical` to the default tier because they cover webhook compatibility and GitHub reporting/release-routing behavior rather than install/upgrade or safety-sensitive gates.
