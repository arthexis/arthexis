# Changelog regeneration audit

## Execution
- Ran `./scripts/generate-changelog.sh` to rebuild `CHANGELOG.rst` using the current repository history and previously recorded contents.

## Observed differences
- The regenerated file prepended commits 8baa8e23 through 0572a5e9 to the "Unreleased" section, duplicating the existing entries from the 0.1.16 release and earlier while leaving no dedicated heading for `v0.1.16 (2025-10-15)`.
- The entire `v0.1.16 (2025-10-15)` section from the prior changelog disappeared, so the documented history of that release is no longer present after regeneration.

## Diagnosis
- Manual inspection of the regenerated diff shows that `_merge_sections` appended every entry from the previous `0.1.16` release into "Unreleased" and skipped re-attaching the `v0.1.16` section, matching the branch in `_merge_sections` that reopens the latest release when no dedicated section exists in the new run.
- Calling `determine_range_spec(previous_text=...)` directly returns `ec77114c..HEAD`, so range selection appears to resolve correctly—the regression stems from the merge step rather than the range calculation itself.

## Potential follow-up tasks
1. Tighten `_merge_sections` so that the "reopen the latest release" branch only runs when explicitly requested (for example, when the builder is preparing release notes), preventing routine recalculations from migrating `0.1.16` entries back into "Unreleased".
2. Backfill a regression test that covers changelog regeneration when no commits have been added since the last release and when commits exist, asserting that the latest tagged release remains in its own section.

## Follow-up implementation
- `_merge_sections` now accepts an explicit `reopen_latest` toggle that defaults to `False`, so routine changelog regenerations keep the most recent tagged release in its dedicated section while release retry flows can still opt-in to reopening.
- Added regression coverage that exercises regeneration both with and without new commits to confirm the latest release section persists unless a caller explicitly requests a reopen.
