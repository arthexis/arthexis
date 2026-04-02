# Web sampler retirement

> **Non-canonical reference:** This document is retained for internal or historical context and is not part of the canonical Arthexis documentation set.

Arthexis no longer executes stored shell-style cURL samplers from the content app.

## What changed

- The `WebRequestSampler` and `WebRequestStep` models were retired.
- The generic scheduler hook that executed `apps.content.tasks.run_scheduled_web_samplers` was removed.
- Historical `WebSample` and `WebSampleAttachment` rows are removed.

This keeps Arthexis aligned with the suite's role as an integration pivot: live collection should happen in a dedicated app or integration module with typed request settings and controlled HTTP clients, not by storing shell commands in the database.

## Upgrade path for existing installations

Apply the content migrations that retire generic sampler models and then drop historical sampler rows. After upgrade, only `ContentSample` data remains for retained artifacts such as screenshots, uploaded files, and audio captures.

## Operator guidance

If a former sampler represents an integration you still need in production, implement it as a dedicated collector in the relevant Django app with explicit request fields such as URL, method, headers, credentials source, and parser strategy.
