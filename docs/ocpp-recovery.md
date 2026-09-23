# OCPP Recovery Architecture

Arthexis treats SQL as the authoritative source of truth for both inbound OCPP
replay and outbound charger operations. Channels owns live WebSocket transport;
in-memory correlation and Redis-backed transport state are acceleration only.

## Outbound durable lifecycle

Every explicit CSMS-to-charger command is retained as a `ProtocolOperation`
before transport delivery is attempted.

```text
PENDING
  durable intent exists; no charger send has begun
    |
    v
DELIVERING
  one consumer owns an actual send attempt
    |
    +---- CallResult ----------------------> COMPLETED
    |
    +---- CallError -----------------------> ERRORED
    |
    +---- timeout / connection ambiguity --> RECOVERY_REQUIRED
```

A successful channel-layer enqueue does not mean the charger received the
command. The operation remains `PENDING` until the owning WebSocket consumer
claims it immediately before the actual OCPP send.

## Attempt identity and ownership

Each actual send attempt receives:

- a fresh `attempt_token`;
- a `delivery_owner`, normally the owning Channels consumer/channel;
- durable first/last attempt timestamps and attempt count.

Only the currently persisted attempt token may settle a `DELIVERING`
operation. A late result, error, timeout, or disconnect from a superseded
attempt is ignored.

Connection ownership is also enforced in the in-process sender registry. If a
new charger connection replaces an older one, a late disconnect from the old
consumer cannot remove the new sender.

## Recovery policy

Each new operation snapshots one recovery policy at creation time.

### SAFE_RETRY

Reserved for observational requests whose duplicate execution does not mutate
charger state, for example configuration/variable reads or composite schedule
queries.

An ambiguous SAFE_RETRY operation may be returned from
`RECOVERY_REQUIRED` to `PENDING` and attempted again.

### RECONCILE

Used for state-changing commands where Arthexis should inspect durable charger,
transaction, reservation, profile, authorization, configuration, or other
domain evidence before deciding whether another command is appropriate.

These operations remain `RECOVERY_REQUIRED` until reconciliation explicitly
resolves them.


For remote start and remote stop commands, Arthexis performs a bounded periodic
SQL reconciliation pass. It settles an ambiguous remote start only when a
matching retained transaction began after the ambiguous attempt, and settles an
ambiguous remote stop only when the targeted retained transaction is durably
completed. Missing evidence never means failure and never triggers an automatic
resend.

A reconciled operation records `reconciled_at`,
`reconciliation_resolution`, and `reconciliation_basis`. Its
`response_payload` remains empty, so a state-derived resolution cannot be
mistaken for the original charger CallResult. `reconciliation_checked_at`
also records unsuccessful reconciliation passes and is used to rotate bounded
recovery work fairly.

### MANUAL

Used for opaque or externally consequential commands such as reset, unlock,
firmware/update operations, and vendor-specific data transfer.

These operations are never automatically replayed after ambiguous delivery.

Existing rows created before recovery-policy persistence migrate conservatively
to MANUAL.

## Reconnect and restart behavior

After a charger WebSocket is accepted, recovery runs asynchronously so the
handshake is not delayed.

For the connected charger and protocol version:

```text
PENDING
  known unsent
  -> send through the fresh owning consumer

DELIVERING owned by this same consumer
  -> leave in flight

DELIVERING owned by another/previous consumer
  -> convert to RECOVERY_REQUIRED
  -> apply persisted recovery policy

RECOVERY_REQUIRED + SAFE_RETRY
  -> PENDING
  -> retry with a new attempt token

RECOVERY_REQUIRED + RECONCILE
  -> retain ambiguity

RECOVERY_REQUIRED + MANUAL
  -> retain ambiguity
```

Repeated reconnect recovery is idempotent for completed operations.

## Failure semantics

### Process loss before send

The operation remains `PENDING` in SQL. A later charger reconnect can deliver
that known-unsent intent, regardless of recovery policy.

### Process loss after a send may have reached the charger

A stranded `DELIVERING` operation is ambiguous. A new owner converts it to
`RECOVERY_REQUIRED` before applying the persisted action-specific policy.

### Lost response

Observational SAFE_RETRY actions can be retried. State-changing or opaque
commands are not blindly replayed.

### Redis or in-memory state loss

Durable operation intent, attempt history, recovery policy, and terminal
outcomes remain in SQL. Transient transport state can be rebuilt when the
charger reconnects.

## Operator interpretation

`PENDING` means Arthexis still has known-unsent work.

`DELIVERING` means one live consumer owns an active attempt.

`RECOVERY_REQUIRED` means the outcome is uncertain. Operators should inspect
the recovery policy before taking action:

- SAFE_RETRY can be retried automatically by reconnect recovery;
- RECONCILE requires authoritative state/evidence;
- MANUAL requires explicit operator judgment.

`COMPLETED` and `ERRORED` are durable terminal outcomes.

Legacy `TIMED_OUT` and `DISCONNECTED` values may still exist on older rows,
but new ambiguous delivery outcomes use `RECOVERY_REQUIRED`.

## Testing contract

Source-owned transport/domain tests cover detailed transitions, policy
classification, attempt-token guards, and ownership races.

The integration acceptance flow in
`tests/integration/ocpp/test_outbound_recovery_flow.py` proves the complete
fresh-WebSocket recovery boundary:

- process loss before send recovers durable PENDING intent;
- process loss after possible delivery retries a SAFE_RETRY query;
- an ambiguous MANUAL command is not emitted again;
- charger CallResult correlation settles the durable SQL row after reconnect.

Future outbound changes should preserve these scenarios and keep SQL
authoritative over transient transport state.
