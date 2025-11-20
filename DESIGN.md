# Design Guidelines

## General UI Principles
- Treat `DESIGN.md` as the canonical reference for interface expectations. Any UI change should be cross-checked here before submitting a patch.

## Favicons
- All favicon assets must share the same rendered radius. When exporting favicons, keep the artwork centered on a square canvas and scale it so it fills the entire available area.
- Aim for the maximum allowed favicon size for each target surface. Avoid excessive transparent padding that shrinks the rendered circle in browser tabs.
- Update every variant (site defaults, role-specific icons, and admin overrides) together so they remain visually consistent.

## Admin Interface
- Do not add new buttons to the Home row of `pages/templates/admin/index.html` unless explicitly requested by the user.
