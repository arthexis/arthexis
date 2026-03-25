# Prompts app removal and archival path

The runtime `prompts` Django app has been removed from automatic discovery and replaced with the legacy migration-only app `apps._legacy.prompts_migration_only.apps.PromptsMigrationOnlyConfig`.

## What we preserved

The deleted seed fixtures were developer-facing change records rather than end-user cookbook content, so their useful narrative was preserved here instead of under `apps/docs/cookbooks/`.

### Historical prompt record: create prompts app with stored prompt workflow

- Original intent: preserve the developer's original request plus a refined implementation plan after work is complete.
- Historical implementation plan:
  1. Create a dedicated prompts app and register a `StoredPrompt` model in admin.
  2. Persist the original request plus a refined implementation plan in seed fixtures.
  3. Capture related file context and optional change references.
  4. Keep future prompt fixtures aligned with the model.

## Release note for operators

If production databases still contain `prompts_storedprompt` rows, upgrade through the release that ships the legacy migration-only prompts app.
That release archives rows into `prompts_archivedstoredprompt` before dropping the live `prompts_storedprompt` table.

If you want a flat-file export before upgrading, run this on a pre-upgrade release:

```bash
./env-refresh.sh --deps-only
.venv/bin/python manage.py dumpdata prompts.StoredPrompt --indent 2 > prompts-export.json
```

Reversing the archival migration restores `prompts_storedprompt` and copies archived rows back into it.
Fresh installs should not discover or enable the historical runtime package.
