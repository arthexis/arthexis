# Agent Guidelines
- Note: do not modify README files unless directed. Use Django's admindocs for app documentation.
- Application documentation lives in Django's admindocs. Do not create or modify per-app README files.
- Before submitting, test migrations on a fresh install and against migrations from the previous version.
- Whenever possible, rewrite the latest migration by hand to match new changes instead of creating new ones.
  Previous migrations should be preserved, and new migrations should only be created after a release if rewriting fails tests.
- Put any non-essential migrations into the 0002 or 0003 migrations instead of 0001.
- Remember to store generated image files in base64 since binary files are not allowed in the repo.
- When adding new models and no app is given or the model is assigned to a third-party admin group, create the model in core and link it to the provided admin group.
- Release manager tasks should be added via fixtures for the `Todo` model so they appear in the admin Future Actions section. Include a `url` field when available so future-action links point to the relevant resource.
- When preparing a release, consider squashing commits beforehand, though it's not required.
- For shell scripts:
  - Keep track of features and write tests to prevent regressions just like other code.
  - Follow consistent naming conventions, using the `.sh` extension with kebab-case names.
  - Keep the interface and meaning of flags consistent across scripts.

