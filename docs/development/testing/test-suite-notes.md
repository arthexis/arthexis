# Test Suite Notes

This document tracks focused test-suite decisions that are too specific for `tests/README.md` but still useful for maintainers.

## Current notes

- The env refresh integration test was retired because CI and local workflows already run environment refresh before test execution, so failures now surface earlier.
- Document rendering integration coverage moved to higher-level doc generation checks and manual QA for end-to-end output, while unit coverage retains critical HTML escaping checks for plain text.
