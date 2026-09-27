# Events and Celery

Arthexis uses durable local state first and Celery for asynchronous delivery and
maintenance. The application database and domain-specific retained evidence are
authoritative; Redis/Celery are recoverable secondary infrastructure.

This document inventories the event streams and Celery jobs currently implemented.
It is descriptive of the codebase, not a list of planned event types.

## Event lifecycle

Domain producers call `publish()` or `publish_safely()` to create an
`EventEnvelope` in SQL. New envelopes start as `pending`.

Celery Beat runs `events.dispatch_pending` every 30 seconds. The dispatcher claims
a bounded batch, then enqueues `events.process` with only the durable
`event_id`. Workers reload the envelope from SQL and invoke handlers registered
for its `event_type`.

Broker handoff is at-least-once. Consumers must therefore be idempotent. Failed
broker handoffs are retained and retried with bounded backoff; stale
`dispatching` claims can be reclaimed after five minutes.

`published_at` means Celery accepted the processing task. It does not mean every
subscriber completed.

## Event streams

| Event type | Producer | Authoritative source | Payload / reference | Current consumer |
| --- | --- | --- | --- | --- |
| `ocpp.meter_values.received` | OCPP metering intake (`apps.ocpp.services.metering`) | Retained `MeterReadingBatch` / transaction meter evidence | Meter batch ID, charger ID, transaction ID when applicable, EVSE ID | `apps.ocpp.subscribers.process_meter_values_received`; recomputes retained transaction energy when a transaction ID is present |
| `discovery.event` | Discovery projection (`apps.ocpp.services.discovery_handoff`) | Filesystem discovery session `events.jsonl` | Session ID, sequence, discovery event type/kind, optional artifact reference | No built-in domain subscriber yet; intended for independent live observers while full evidence is retrieved from the discovery report |

### OCPP meter events

Standalone retained OCPP 2.0.1 meter intake persists the meter batch and its
protocol result first. Publication is scheduled with `transaction.on_commit`, so
secondary processing cannot precede the authoritative database commit.

The registered OCPP subscriber listens for `ocpp.meter_values.received`. If the
event identifies a transaction, it recomputes transaction energy from retained
meter evidence. Events without a transaction ID are valid and require no
transaction recomputation.

### Discovery events

Discovery has a different source of truth: each discovery session has an
append-only filesystem `events.jsonl`. An observation is flushed and fsynced there
before live projection is attempted.

The projected `discovery.event` deliberately does not copy packet captures,
OCPP payloads, notes, or other potentially large evidence. Consumers receive a
stable session ID and sequence number and can retrieve the durable event/report
by those identifiers. An artifact path may be included as a reference.

Projection is best-effort. Failure to create the SQL event envelope, failure of
Celery, or broker outage must not invalidate or stop the discovery session.
Filesystem evidence remains retrievable independently.

Discovery event subtypes currently come from the discovery evidence stream and
include lifecycle/observation names such as `session_started`,
`traffic_observed`, `dns_query`, `connection_attempt`, `csms_candidate`,
`capture_started`, `capture_failed`, `redirect_observed`,
`ocpp_connection`, `capture_succeeded`, and `session_completed`. They are
carried in the `event_type` field of the outer `discovery.event` payload rather
than becoming separate SQL event-envelope types.

## Celery tasks

### Event delivery

| Task | Trigger | Purpose |
| --- | --- | --- |
| `events.dispatch_pending` | Celery Beat, every 30 seconds | Claims pending/failed/stale event envelopes and hands their IDs to the broker |
| `events.process` | Enqueued by the event dispatcher | Reloads one `EventEnvelope` from SQL and dispatches it to registered subscribers |

### OCPP maintenance and reconciliation

| Task | Scheduled today | Purpose |
| --- | --- | --- |
| `ocpp.maintenance.refresh_stale_connections` | Every hour | Clears stale charger connection state older than two hours |
| `ocpp.maintenance.reconcile_meter_energy` | Every 60 seconds | Repairs stale transaction energy from authoritative retained meter evidence |
| `ocpp.maintenance.reconcile_session_operations` | Every 60 seconds | Reconciles ambiguous remote start/stop operations from retained session evidence |
| `ocpp.maintenance.reconcile_configuration_operations` | Not in the base Beat schedule | Reconciles ambiguous configuration writes and dispatches required observation operations |
| `ocpp.maintenance.reconcile_availability_operations` | Not in the base Beat schedule | Reconciles ambiguous connector availability operations |
| `ocpp.maintenance.reconcile_reservation_operations` | Not in the base Beat schedule | Reconciles ambiguous reservation mutations |
| `ocpp.maintenance.reconcile_profile_operations` | Not in the base Beat schedule | Reconciles ambiguous charging-profile mutations |
| `ocpp.maintenance.reconcile_local_list_operations` | Not in the base Beat schedule | Reconciles ambiguous local-list writes and dispatches required version observations |

The unscheduled maintenance tasks are Celery-capable entry points but are not
periodic jobs in the base `CELERY_BEAT_SCHEDULE`. Deployment-specific orchestration
may invoke them explicitly; documentation should not imply that Beat runs them
unless the schedule changes.

## Producers and consumers

Event producers own authoritative state changes; events describe work or facts
that have already been retained. A producer must not require a live broker to
complete a valid charger exchange.

Consumers are registered in-process through `apps.events.registry.subscribe`.
They receive the full SQL `EventEnvelope`, not a broker copy of its payload.
Handlers must be idempotent because delivery is at-least-once.

OCPP registers its subscribers through `apps.ocpp.subscribers`. Discovery
currently has no mandatory built-in subscriber: this is intentional. LCD, audio,
remote monitoring, Watchtower ingestion, or other observers can consume the
stable discovery reference contract without becoming part of discovery
persistence or charger operation.

## Failure boundaries

For live OCPP and discovery paths:

1. perform reply-critical validation and authoritative state changes;
2. durably retain domain/discovery evidence;
3. project a compact event when useful;
4. never wait for external consumers to finish before acknowledging valid
   charger work.

`publish()` is the strict event API and propagates validation/database errors.
`publish_safely()` converts expected serialization/database publication failures
to `None` after logging. Callers on especially sensitive paths may additionally
guard projection so unexpected secondary failures cannot break the authoritative
operation.

Celery/Redis failure can delay event consumers and maintenance jobs, but it must
not erase retained OCPP state or filesystem discovery evidence.
