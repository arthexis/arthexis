# VERSION planning for 1.0.0

Date: 2026-03-27.

## Planned version marker

- Current repository marker: `0.2.3`.
- Planned release marker for baseline reset: `1.0.0`.

## Planning assumptions

1. Set `VERSION` to `1.0.0` only in the baseline-reset release change set.
2. Treat major-version behavior in `config/settings/apps.py` as authoritative for dropping legacy app and migration shims.
3. Publish `1.0.0` as fresh-install/import only.
4. Do not advertise in-place upgrade from `0.x` into the reset baseline.

## Release checklist note

Before tag and package:

- confirm allowlist in `docs/development/1.0-cleanup-allowlist.md`
- confirm migration freeze window remained in force until baseline-reset merge
- update `VERSION` from `0.2.3` to `1.0.0` in the release commit
