No agent-specific guidance is currently defined for this repository.

## Admin UI enforcement recommendation
- Prefer shared admin primitives from `core/admin_ui_framework.css` for any admin template work.
- Avoid adding inline `<style>` blocks or template-local stylesheet links in admin templates unless absolutely necessary.
- If custom CSS is truly required in a new admin template, include the explicit marker `admin-ui-framework: allow-custom-css` with rationale so enforcement remains intentional.

