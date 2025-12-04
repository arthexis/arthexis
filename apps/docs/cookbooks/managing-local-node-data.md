# Managing Local Node Data

This guide explains how Arthexis automatically loads bundled fixtures on each node and how those seed records relate to user-generated data stored on disk.

## How seed fixtures load

- The maintenance task in [`env-refresh.py`](../../env-refresh.py) scans every `fixtures/*.json` file under the project root and calculates a hash so it only reloads when fixture content or migrations change. During reload, fixtures are sorted to satisfy dependencies (for example, user and group records load before module and landing pages).
- Each fixture is patched on the fly to avoid duplicate users, update many-to-many links, and normalize `Site` records. Any model exposing an `is_seed_data` field is forced to `true` as the objects are written, ensuring the database tracks which rows came from the shipped dataset.
- After the core fixtures finish loading, the task refreshes landing defaults, recreates sigils, and writes the new fixture hash to `.locks/fixtures.md5` so subsequent runs can skip unchanged data.

## Relationship to user data

- Shared user fixtures stored directly under `data/*.json` are loaded once per process start, and per-user fixtures under `data/<username>/*.json` are loaded for each account. The loader skips `user_*` files to avoid overwriting the authenticated user entries.
- Freshly created staff accounts automatically pull in both shared and personal fixtures via signals in [`apps/locals/user_data.py`](../../apps/locals/user_data.py) so admins inherit the latest local defaults on first login.
- Exporting or importing user data through the admin uses the same on-disk files. User data fixtures override or extend the seed set without modifying the shipped JSON files, keeping customizations isolated to the node.

## Operational tips

- When you change fixture contents, rerun `env-refresh.py database --latest` (or the platform’s install/upgrade scripts) so the hash changes and the auto-loader reapplies the seed data.
- To seed new reference rows, add them to a `fixtures/*.json` file and ensure `is_seed_data` is set. The loader will mark it for you if the field exists, but explicit flags help track intent in version control.
- Keep user data for site-specific tweaks or test records. Because it sits beside the database on disk, you can back up `data/` and move it between nodes without touching the versioned seed fixtures.
