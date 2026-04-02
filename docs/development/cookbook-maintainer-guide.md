# Cookbook Maintainer Guide

This guide defines the repeatable workflow for keeping Arthexis cookbooks accurate and useful for operators, administrators, and integrators.

## Review cadence

Run cookbook maintenance during:

- release preparation;
- large refactors that move files, commands, or routes;
- integration changes that alter admin workflows or model boundaries.

## Standard audit workflow

1. Open the [Cookbook QA checklist](../../apps/docs/cookbooks/cookbook-qa-checklist.md).
2. Review each impacted cookbook against every checklist section.
3. Update the cookbook and related fixture metadata when titles, slugs, or file names change.
4. Capture findings and follow-up actions in the PR description using the checklist output template.

## Scope expectations

- Keep cookbook guidance centered on first-class Arthexis suite components.
- Prefer references to maintained apps, models, migrations, admin tools, and management commands.
- Avoid documenting one-off operator workarounds when a reusable suite integration is the right fix.

## Quick links

- [Cookbook library fixtures](../../apps/docs/fixtures/cookbook__docs_cookbook.json)
- [Cookbook QA checklist source](../../apps/docs/cookbooks/cookbook-qa-checklist.md)
- [Arthexis documentation index](../index.md)
