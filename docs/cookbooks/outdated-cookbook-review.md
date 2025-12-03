# Cookbook audit: candidates for removal

A quick review of the `docs/cookbooks/` directory surfaced several entries that point to code paths that no longer exist in the repository. Because the instructions are now misleading, they are strong candidates for deletion unless they are rewritten to match the current layout.

| Document | Evidence of staleness | Recommendation |
| --- | --- | --- |
| `favorites.md` | References templates and admin modules under `pages/` (for example `pages/templates/admin/...` and `../../pages/admin.py`), but the code now lives under `apps/pages/`, so every quoted path is broken. 【F:docs/cookbooks/favorites.md†L14-L52】 | Remove the cookbook or rewrite it with the correct `apps/pages/` paths and refreshed line references. |
| `node-features.md` | Points to `nodes/admin.py`, `nodes/feature_checks.py`, and `nodes/models.py` relative to the repo root, yet those modules exist under `apps/nodes/`, making the inline links inaccurate. 【F:docs/cookbooks/node-features.md†L16-L82】 | Delete or replace with an updated guide that matches the current module locations. |
| `user-data.md` | Directs readers to `locals/user_data.py` and templates under `locals/templates/...`, but the implementation has moved under `apps/locals/`, leaving the links unusable. 【F:docs/cookbooks/user-data.md†L15-L63】 | Drop the page or rebuild it with valid `apps/locals/` references to avoid confusion. |

No other cookbooks showed obvious issues during this pass; the three above stand out because their navigation hints and file links are incorrect for today’s repository layout.
