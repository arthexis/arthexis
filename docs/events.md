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

## Planned evolution

The next phases will turn `EventEnvelope` into a durable outbox, add
restart-safe inbound OCPP replay identities, restore crash-safe transaction
reconciliation, and progressively move secondary charger work behind durable
event processing.

Those changes must preserve the E0 fail-soft hot-path rule.
