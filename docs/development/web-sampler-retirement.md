# Web sampler retirement and historical data access

Arthexis no longer executes stored shell-style cURL samplers from the content app.

## What changed

- The `WebRequestSampler` and `WebRequestStep` models were retired.
- The generic scheduler hook that executed `apps.content.tasks.run_scheduled_web_samplers` was removed, and the temporary compatibility alias task has now been fully removed from `apps.content.tasks`.
- Historical `WebSample` and `WebSampleAttachment` rows remain available in a read-only form.
- Startup now fails during migrations if `django_celery_beat` still contains periodic tasks pointed at the retired alias so operators can migrate each trigger to the owning app's dedicated collector task before rollout.

This keeps Arthexis aligned with the suite's role as an integration pivot: live collection should happen in a dedicated app or integration module with typed request settings and controlled HTTP clients, not by storing shell commands in the database.

## Upgrade path for existing installations

Apply the content migration that retires the sampler models. During migration, Arthexis copies the identifying sampler metadata onto each historical record:

- `WebSample.legacy_sampler_id`
- `WebSample.sampler_slug`
- `WebSample.sampler_label`
- `WebSampleAttachment.legacy_step_id`
- `WebSampleAttachment.step_slug`
- `WebSampleAttachment.step_name`

After the migration:

1. Existing `WebSample` rows still contain their captured `document` payloads.
2. Existing `WebSampleAttachment` rows still point to their `ContentSample` attachments.
3. The former sampler and step tables are removed, so the preserved metadata fields become the stable read-only lookup surface.

## Operator guidance

If operators still need historical access, use one of these approaches after upgrading:

- Browse **Historical Web Samples** in Django admin.
- Export `WebSample` and `WebSampleAttachment` rows through your normal database backup or reporting workflow.
- Follow the preserved `content_sample` relation to inspect or export stored attachments.

If a former sampler represents an integration you still need in production, implement it as a dedicated collector in the relevant Django app with explicit request fields such as URL, method, headers, credentials source, and parser strategy.
