# Versioning and Maturity Policy

This policy defines how Arthexis versions are advanced during the release procedure.

Arthexis versions follow `MAJOR.MINOR.PATCH`.

## Version increment rules

### MAJOR

Increment **MAJOR** when a release adds or removes an app, or drastically changes an app interface.

Examples:

- adding a new app
- deleting an existing app
- drastically changing an app interface

When MAJOR increments, MINOR and PATCH reset to `0`.

### MINOR

Increment **MINOR** when a release includes any inter-app public contract change, including:

- public-facing views, forms, or fields that affect cross-app behavior
- public APIs used across app boundaries
- creating or deleting models within an app
- changes to global settings
- introducing new environment variables

When MINOR increments, PATCH resets to `0`.

### PATCH

Use **PATCH** for all other changes that do not meet MAJOR or MINOR criteria.

Typical PATCH examples include:

- admin-only changes
- scripts and tooling updates
- dependency updates
- documentation updates
- tests and examples
- app seed data changes
- CI/workflow rule updates

## Release procedure ownership

Developers should not manually edit `VERSION` while implementing changes.

The release procedure is responsible for selecting the next version by applying this policy to the full set of changes included in that release. The automated prepare-release workflow encodes the same policy before opening a release PR:

- app additions or removals require MAJOR
- public UI, route, API, serializer, consumer, settings, and model-contract changes require at least MINOR
- docs, tests, scripts, workflow changes, and admin-only changes remain PATCH unless a higher rule also applies

Maintainers can force a higher bump level when the automatic path cannot infer intent, such as a drastic interface change that is not obvious from file paths.

Version advancement is collapsed to a single step per release:

- do not increment once per file or once per app change
- select the highest required bump level found in the release diff
- apply that bump once

Example:

- Current release: `0.2.5`
- Changes before next release: public-facing view changes in three apps
- Required bump: MINOR
- Next release: `0.3.0`

## Summary

- If any MAJOR condition is met, bump MAJOR and reset MINOR/PATCH.
- Else if any MINOR condition is met, bump MINOR and reset PATCH.
- Else bump PATCH.
