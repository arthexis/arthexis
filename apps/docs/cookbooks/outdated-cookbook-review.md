# Cookbook maintenance notes

Arthexis documentation is maintained continuously. When you update or add a
cookbook:

- Verify file and module references still match the current `apps/` layout.
- Prefer linking to stable app entry points (`admin.py`, `models.py`, service
  modules, and user-facing routes) over implementation details that move often.
- Remove cookbook sections that describe retired behavior, and document the
  replacement workflow in the same change.
- Keep examples aligned with current management-command entrypoints and project
  conventions.

If a cookbook becomes obsolete and has no replacement path, delete it rather
than keeping stale instructions in the published library.
