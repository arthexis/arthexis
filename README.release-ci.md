# Release CI

[![Release CI](https://img.shields.io/github/actions/workflow/status/arthexis/arthexis/release-readiness.yml?branch=main&label=Release%20CI&cacheSeconds=300)](https://github.com/arthexis/arthexis/actions/workflows/release-readiness.yml)

The **Release Readiness** workflow runs on pull requests as a non-blocking preflight for the release pipeline.
It mirrors release-critical stages (tests, package build, and publish artifact verification) so issues are surfaced before handoff to the tag-driven release workflow.

> Status behavior: this check is intentionally **informational** for now. Failures are reported, but the workflow is configured as non-blocking while the team tunes stability and coverage.
