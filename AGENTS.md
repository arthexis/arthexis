# Agent Guidelines
- Note: do not modify README files unless directed. Use Django's admindocs for app documentation.
- Application documentation lives in Django's admindocs. Do not create or modify per-app README files.
- Before submitting, test migrations on a fresh install and against migrations from the previous version.
- Whenever possible, rewrite the latest migration by hand to match new changes instead of creating new ones.
  Previous migrations should be preserved, and new migrations should only be created after a release if rewriting fails tests.
- Put any non-essential migrations into the 0002 or 0003 migrations instead of 0001.
- Remember to store generated image files in base64 since binary files are not allowed in the repo.
- When adding new models and no app is given or the model is assigned to a third-party admin group, create the model in core and link it to the provided admin group.
- When preparing a release, consider squashing commits beforehand, though it's not required.

