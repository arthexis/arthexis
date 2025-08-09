# Agent Guidelines

- The top-level `README.md` is auto-generated. **Never edit this file directly.**
- For documentation updates, edit `README.base.md` or the `README.md` inside the relevant app's directory (e.g., `accounts/README.md`).
- The combined `README.md` is regenerated only when creating a release using `python manage.py build_readme`. Do not rebuild it during normal development.
- If migrations fail with `NodeNotFoundError` for `sites.0002_alter_domain_unique`, update any `website` app migration dependencies to use `('sites', '0001_initial')`.

