# GitHub Monitoring Suite Proposal

This proposal defines a **GitHub Monitoring suite** capability for Arthexis, with an initial focus on continuously monitoring configured repositories for issues and emitting signed net messages.

The design keeps Arthexis as the integration pivot: GitHub state is modeled with Django apps/models/migrations, operator workflows live in Django admin, and outbound sharing flows through the existing node net-message channel.

## 1) Feature summary

### Proposed capability

Add a new suite app (recommended label: `ghmon`) that:

1. Tracks one or more configured GitHub repositories.
2. Polls GitHub issue activity continuously using cursor-based synchronization.
3. Stores normalized issue snapshots and change events.
4. Produces signed net messages for downstream nodes/services when configured trigger rules match.
5. Provides admin controls for repository configuration, severity rules, alert routing, and replay.

### Why this belongs in Arthexis

- Reuses Arthexis scheduler/task patterns (periodic jobs + durable state).
- Reuses Arthexis trust and transport (`/nodes/net-message/`) rather than creating side channels.
- Gives administrators first-class control and auditability inside the suite.

## 2) Scope and non-goals

### In scope (phase 1)

- GitHub Issues only (`open`, `closed`, `reopened`, label changes, assignment, and comments metadata summary).
- Polling-based synchronization (webhook support can come later).
- Net-message emission with dedupe key and retry tracking.
- Admin UI for repository config and rule management.

### Out of scope (phase 1)

- Deep PR review analytics.
- Full comment body replication beyond compact excerpts/metadata.
- Bidirectional writeback to GitHub.

## 3) Domain model (Django)

Create app-level models/migrations so the integration remains explicit and durable:

- `GitHubRepositoryMonitor`
  - `owner`, `name`, `api_base_url`, `enabled`
  - token reference/secret pointer (not raw token in plain text)
  - `poll_interval_seconds`, `last_polled_at`, `last_success_at`, `last_error`

- `GitHubIssueCursor`
  - one-to-one with repository monitor
  - cursor fields for incremental sync (`last_seen_issue_updated_at`, `last_seen_issue_id`)
  - backfill controls (`initial_backfill_completed`)

- `GitHubIssueSnapshot`
  - stable GitHub issue id/node id, number, state, labels, assignees, milestone, author
  - timestamps (`created_at`, `updated_at`, `closed_at`)
  - normalized priority fields for local routing

- `GitHubIssueEvent`
  - event type (`opened`, `closed`, `reopened`, `labeled`, `unlabeled`, `assigned`, `commented`)
  - computed `severity`, `impact_tags`, and `event_hash` (dedupe)
  - foreign key to monitor + issue snapshot

- `GitHubNetMessageDelivery`
  - links `GitHubIssueEvent` to node message ids
  - tracks delivery status, retries, last attempt, terminal error

This data model enables deterministic replay and audit trails, which is key for an operational monitoring feature.

## 4) Continuous monitoring flow

### Scheduler

- Register a periodic task (Celery beat pattern already used in suite).
- Default cadence: every 60 seconds per enabled repository (configurable).
- Use per-repository lock to avoid overlapping polls.

### Poll algorithm

1. Load repository monitor + cursor.
2. Query GitHub issues API with `since=last_seen_issue_updated_at` and deterministic sort.
3. Upsert issue snapshots.
4. Compute semantic events by diffing prior snapshot vs current payload.
5. Evaluate routing rules/severity policy.
6. Emit net message(s) for matching events.
7. Record delivery outcomes and advance cursor only after durable write.

### Failure handling

- Exponential backoff per repository on API errors.
- Preserve cursor on partial failure to avoid missed events.
- Idempotency by `event_hash` and delivery dedupe key.

## 5) Net message contract

Emit through existing node transport with a dedicated message kind:

- `kind`: `github.issue.event.v1`
- `topic`: `github/<owner>/<repo>/issues`
- `dedupe_key`: `<repo_id>:<issue_id>:<event_type>:<updated_at_iso>`

Example payload (shape):

```json
{
  "kind": "github.issue.event.v1",
  "repository": "arthexis/arthexis",
  "issue": {
    "number": 482,
    "title": "Charging profile regression on reconnect",
    "state": "open",
    "labels": ["bug", "priority:high"]
  },
  "event": {
    "type": "labeled",
    "severity": "high",
    "occurred_at": "2026-04-15T11:22:33Z"
  },
  "links": {
    "html_url": "https://github.com/arthexis/arthexis/issues/482",
    "api_url": "https://api.github.com/repos/arthexis/arthexis/issues/482"
  },
  "source": {
    "node": "suite-host-01",
    "monitor_id": 7
  }
}
```

## 6) Admin/operator UX

Add admin views for:

- Repository monitors (enable/disable, token reference, cadence, health).
- Rule presets (which labels/states map to severity and outbound routing).
- Recent event feed with delivery status and replay action.
- Dry-run test action to validate API access and rule matching.

Operator controls should remain powerful (no artificial lock-down), with explicit safeguards only around credential handling and outbound trust targets.

## 7) Security and compliance

- Use least-privilege GitHub tokens (repo metadata + issues read).
- Keep token material in configured secret backend; store only references in DB.
- Redact sensitive fields in logs and admin history.
- Sign net messages using existing suite trust mechanism.
- Store enough metadata for forensics (request id, response status, truncated error body).

## 8) Feature flags and rollout

- `github-monitoring-suite` (master switch)
- `github-monitoring-issues` (phase-1 issue polling)
- `github-monitoring-net-message` (outbound relay)

Rollout plan:

1. **Phase A**: data model + manual sync command.
2. **Phase B**: periodic polling + admin health/status.
3. **Phase C**: rule engine + net-message emission + replay tooling.
4. **Phase D**: optional webhook ingestion to reduce API polling volume.

## 9) Testing strategy

- Model tests for cursor advancement and event dedupe.
- Service tests for issue diffing and severity mapping.
- Task tests for retry/backoff + lock behavior.
- Transport tests for net-message payload shape and signature invocation.
- Integration test with mocked GitHub API pagination and failure sequences.

## 10) Acceptance criteria (phase 1)

1. Admin can configure a repository monitor and verify connection.
2. New/changed issues are persisted in local snapshots within one polling cycle.
3. Matching issue events produce exactly one deduplicated net message per semantic event.
4. Delivery failures are visible and replayable from admin.
5. No raw access tokens are stored in plain text model fields.

---

If approved, the next step should be implementation as a dedicated app with migrations, admin wiring, periodic tasks, and endpoint documentation updates to keep the suite aligned with Arthexis integration standards.
