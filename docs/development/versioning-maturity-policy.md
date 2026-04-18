# Versioning and Maturity Policy

This policy explains how Arthexis uses a versioning scheme inspired by semantic versioning to communicate release confidence and operational readiness.

Arthexis is currently in the `0.2.x` track, which maps to the **Beta** maturity stage defined below.

## Policy goals

- Make maturity visible in every released version string.
- Provide predictable promotion criteria from one maturity stage to the next.
- Keep operators, integrators, and contributors aligned on what each release guarantees.

## Version format

Arthexis versions follow `MAJOR.MINOR.PATCH`.

- **MAJOR**: Breaking changes that define a new generation of the suite.
- **MINOR**: Maturity stage for the current major-zero line.
- **PATCH**: Iterative progress steps within the active maturity stage.

## Minor maturity stages

Within the current `0.x` line, minor values map to fixed maturity meanings:

| Minor | Maturity stage | Meaning |
| --- | --- | --- |
| `.0` | Experimental | New direction, exploratory integrations, and rapid learning cycles. |
| `.1` | Preview | Early adopter quality with key workflows available for controlled pilots. |
| `.2` | Beta | Broad feature readiness with active hardening, compatibility checks, and feedback incorporation. |
| `.3` | Release Candidate | Stabilization-focused builds intended to validate production readiness. |
| `.4` | Stable (GA) | General Availability with conservative change management and reliability focus. |

> Example: `0.2.7` means "Major 0, Beta maturity, patch step 7 in Beta."

## Patch step meaning

Patch increments are variable, practical steps toward maturity completion.

Each patch can deliver one or more of the following:

- defect fixes and regression cleanup
- security hardening
- operational reliability improvements
- integration compatibility updates (OCPP, external services, node roles)
- admin UX and workflow polish
- documentation and migration clarifications

Patch numbers are **not** time-based and are **not** interpreted as risk scores by themselves.
They indicate progression inside the current maturity stage.

## Promotion criteria between maturity stages

Promotion to the next minor stage should be based on evidence, not calendar pressure.

A release manager should confirm:

1. Relevant tests pass for the targeted scope.
2. Known regressions are triaged and documented.
3. Migration and upgrade paths are validated for representative deployments.
4. Core operator/admin workflows remain functional.
5. OCPP and integration behaviors covered by the release are validated.
6. Release notes clearly describe known limitations and upgrade considerations.

## Release note convention

Each release note should include:

- current maturity stage
- shipped changes in that release
- known constraints, limitations, and upgrade considerations

Release notes are a factual record of what shipped and what operators should account for now. They are not a roadmap, forecast, or commitment to future milestones.

This policy is governance and reference guidance for consistent maturity signaling across the Arthexis suite.
