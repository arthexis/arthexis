# Events Architecture

The `events` app is Arthexis' durable domain-event boundary.

## Current E0 contract

Today, publishing an event means validating a JSON-compatible mapping and
persisting an `EventEnvelope` in the application database. Event persistence
is local database work; it is not a Celery or broker operation.

`publish()` is the strict API. Invalid event metadata, non-serializable
payloads, or database failures propagate to the caller.

`publish_safely()` is the fail-soft API for protocol and other latency-sensitive
paths. Expected validation, serialization, and database failures are logged and
converted to `None`. A secondary event failure must not turn an otherwise valid
OCPP exchange into a charger communication failure.

`published_at` is reserved for the durable delivery lifecycle introduced by
the outbox work. E0 does not mark events published and does not couple event
creation to Celery.

## Hot-path rule

For OCPP and other live protocol handlers:

1. perform only reply-critical validation and authoritative state changes;
2. persist any domain event locally when useful;
3. return the protocol response without waiting for external brokers, email,
   analytics, or other secondary systems.

The database is authoritative. Celery and Redis must remain recoverable
secondary infrastructure rather than prerequisites for a valid immediate
charger response.

## E1 durable outbox

E1 turns `EventEnvelope` into a SQL-backed outbox without changing the publish
API. New events begin in `pending`. A Celery Beat task periodically claims due
events in short database transactions, releases database locks, then hands only
the durable `event_id` to the broker.

Successful broker handoff marks the event `published` and sets
`published_at`. Broker failures become retryable `failed` rows with bounded
backoff. A process that dies after claiming work leaves a `dispatching` event;
claims older than five minutes are stale and can be recovered by a later pass.

The broker handoff is at-least-once. A crash after the broker accepts a task but
before SQL records `published` can cause the same event to be handed off again.
Downstream event consumers therefore must be idempotent.

The generic worker reloads the full envelope from SQL by `event_id` and invokes
registered consumers. SQL remains the source of truth; broker messages carry no
copy of the domain payload.

`published_at` means that asynchronous processing was successfully handed to
Celery. It does not mean that every eventual subscriber completed. Per-consumer
delivery state can be introduced later if real consumers require that stronger
contract.

## Planned evolution

The next phases add restart-safe inbound OCPP replay identities, restore
crash-safe transaction reconciliation, and progressively move secondary charger
work behind durable event processing.

Those changes must preserve the E0 fail-soft hot-path rule.
