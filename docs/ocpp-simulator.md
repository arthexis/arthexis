# Live OCPP charge-point simulator

Arthexis includes a model-independent OCPP 1.6J client for exercising a real
CSMS over the network. The live simulator represents the charger. It must not
reach into the target satellite's database, Redis, Celery, or event bus.

The operator surface is the `ocpp_simulator` Django management command. Gway
can ingest that command in the same way as other Arthexis management commands.

## GWAY-001 → GW004 field test

The field-facing interface is endpoint-driven. GW001 behaves like a normal
charger: it knows its own charger identity and the CSMS URL, but it does not
need to know that the remote node happens to be GW004.

With Arthexis mounted in Gway, the intended operator flow on GW001 is:

```console
gway arthexis ocpp simulator start ws://192.168.129.10:9000
gway arthexis ocpp simulator authorize TEST001
gway arthexis ocpp simulator replay reconciled.sqlite3
gway arthexis ocpp simulator status
gway arthexis ocpp simulator stop
```

`start` derives the simulated charger identity from
`ARTHEXIS_OCPP_SIMULATOR_IDENTITY` when set, otherwise from the local short
hostname. Pass `--charger ...` only when an explicit override is needed.

For a literal private, loopback, or link-local IP address, `ws://` is accepted
automatically for the direct field-test network. Plaintext WebSocket to a
hostname or non-local address still requires explicit `--allow-insecure-ws`.
Production-style endpoints should use `wss://`.

The equivalent direct Django command remains available for debugging and
backward compatibility:

```console
python manage.py ocpp_simulator start ws://192.168.129.10:9000
```

`start` establishes the WebSocket, negotiates `ocpp1.6`, sends
`BootNotification`, requires an `Accepted` boot result, and leaves a local
worker owning that same connection. Production-style endpoints should use
`wss://`; insecure `ws://` is rejected unless explicitly allowed.

Send an arbitrary RFID/idTag without predicting the authorization result:

```console
python manage.py ocpp_simulator authorize TEST001
```

The JSON response reports the actual OCPP authorization status returned by
GW004. Raw idTags are deliberately not written to the worker status surface.

## Authorization policy matrix

After opening the persistent simulator connection, run the standard policy
matrix with operator-selected tag roles. The simulator does not infer the
expected result; GW004 remains the system under test and returns the actual OCPP
status for each attempt.

For a charger configured with open authorization mode:

```console
python manage.py ocpp_simulator authorize-scenario \
  --charger GWAY001 \
  --policy-context open \
  --known-authorized KNOWN_OK \
  --known-denied KNOWN_DENIED \
  --unknown UNKNOWN_TAG
```

For restricted mode, use the same real tag roles and change only the descriptive
policy context:

```console
python manage.py ocpp_simulator authorize-scenario \
  --charger GWAY001 \
  --policy-context restricted \
  --known-authorized KNOWN_OK \
  --known-denied KNOWN_DENIED \
  --unknown UNKNOWN_TAG
```

The matrix runs four attempts in order: known-authorized, known-denied, unknown,
and a repeat of known-authorized. Human-readable output is the default. Add
`--json` for the complete privacy-safe machine-readable result.

Result records include scenario name, attempt name, sequence, observed status or
transport error, repeat identity, and policy context. They do not include the
raw RFID/idTag values supplied on the command line.

Run both policy contexts against the corresponding real Arthexis configuration
when comparing behavior. The simulator intentionally does not encode what
`open` or `restricted` should return, so a policy change in Arthexis remains
observable rather than being duplicated in test code.

Inspect or deliberately cycle the connection:

```console
python manage.py ocpp_simulator status
python manage.py ocpp_simulator reconnect
python manage.py ocpp_simulator stop
```

`reconnect` closes the old transport, opens a new WebSocket for the same
charger identity, and performs `BootNotification` again. This is the network
seam used by later reconnect-and-drain backlog tests.

## Physical acceptance

The first hardware acceptance is intentionally end-to-end:

```text
GWAY-001 simulator
  → OCPP WebSocket
  → GW004 OCPP ingress
  → authorization policy
  → durable/local event processing
  → display projection
  → gway-epaper
  → physical e-paper
```

A successful `Authorize` response alone proves the protocol and policy path.
The field test is complete only after the corresponding authorization activity
is also visible through GW004's operator-status/display path. Display failure
must not affect the OCPP response or charger processing.

## Lifecycle and failure behavior

The worker closes automatically after the configured control idle timeout
(default 300 seconds). Heartbeats use the interval supplied by
`BootNotification` and do not count as operator activity. A heartbeat failure
ends the worker rather than leaving stale "connected" metadata.

Malformed or uncorrelated server frames do not satisfy pending calls. Matching
OCPP `CallError` frames are returned to the operator as command errors.
Server-initiated actions not implemented by the simulator receive
`NotSupported` rather than being silently ignored.


## Field backlog replay

Use the same persistent live simulator connection for field-derived backlog
reproduction. Replay sources are read-only and must already be migrated to the
current-generation Arthexis schema, either as a bare SQLite database or a
finalized replay/capture package that resolves to one.

Replay reconstructed transaction history at charger speed:

```console
python manage.py ocpp_simulator replay /path/to/reconciled.sqlite3 \
  --source-charger FIELD_CHARGER \
  --stream transactions \
  --pacing maximum
```

To reproduce an interrupted drain, reconnect after a fixed number of delivered
events. The simulator closes the live WebSocket, opens a new one for the same
charger identity, performs `BootNotification`, requires `Accepted`, and then
continues the same replay stream:

```console
python manage.py ocpp_simulator replay /path/to/reconciled.sqlite3 \
  --source-charger FIELD_CHARGER \
  --stream transactions \
  --reconnect-after 500
```

Historical payload timestamps remain intact where retained by the replay source;
delivery timing is independent. Use `--pacing fixed --interval-seconds ...`
for a fixed rate, or `--pacing burst --burst-size ... --burst-pause-seconds ...`
for burst delivery. `--batch-size` controls SQLite fetch size rather than
loading the complete history into memory.

Every replay result includes a bounded `metrics` object with attempted,
completed, and failed request counts; elapsed wall-clock time; completed-request
throughput; mean and maximum OCPP request latency; generic transport-failure
count; and error counts grouped by exception type. Each request is timed
individually, but latency samples are not retained, so metrics memory usage does
not grow with replay size.

When `--reconnect-after` actually reaches its checkpoint, the same metrics
also report reconnect attempts, successes, failures, and mean/maximum reconnect
duration. A rejected post-reconnect `BootNotification` is counted as a failed
reconnect. Ordinary request failures remain generic transport failures unless
the transport can prove a reconnect cycle occurred; the simulator does not
infer a disconnect by parsing exception text.

For high-volume runs, the worker retains at most 1,000 action names in the JSON
response. `events_completed` and the metrics counters still report the full
run, while `actions_truncated` indicates whether the `actions` array is only
a prefix. This keeps the operator response bounded even for 100k+ streamed
events.

When the migrated database contains retained charger-originated
`InboundProtocolRequest` payloads, replay those protocol-realistic requests
directly instead of reconstructing transaction history:

```console
python manage.py ocpp_simulator replay /path/to/reconciled.sqlite3 \
  --source-charger FIELD_CHARGER \
  --stream inbound \
  --pacing maximum
```

The two streams have different purposes. `transactions` reconstructs valid
StartTransaction/MeterValues/StopTransaction traffic from retained domain
records and rebinds the remote runtime transaction ID. `inbound` sends only
retained OCPP 1.6 charger-to-CSMS requests whose original request payload was
captured. Neither stream replays CSMS-to-charger operations or invents messages
from incomplete evidence.

For the GWAY-001 → GW004 field test, watch GW004's OCPP/operator status while
the drain runs. A reconnect test is successful only when the post-reconnect
BootNotification is accepted and the remaining backlog continues through the
real GW004 ingress path.


## GW001 → GW004 field validation handoff

Use a direct Ethernet link with GW004 already running the normal Arthexis OCPP
satellite/listener. GW001 should have no special knowledge of the GW004 node
name; only the listener endpoint is configured.

Recommended operator sequence on GW001:

```console
gway arthexis ocpp simulator start ws://192.168.129.10:9000
gway arthexis ocpp simulator status
gway arthexis ocpp simulator authorize TEST001
gway arthexis ocpp simulator replay reconciled.sqlite3
gway arthexis ocpp simulator stop
```

For interrupted-drain validation:

```console
gway arthexis ocpp simulator replay reconciled.sqlite3 --reconnect-after 500
```

A field run is accepted when:

1. `start` returns an accepted BootNotification and the derived GW001 charger
   identity is visible on GW004.
2. `authorize` traverses the real Ethernet/WebSocket OCPP path and GW004
   reports the corresponding authorization activity without simulator-side
   database, Redis, Celery, or event injection.
3. replay consumes the migrated/current-generation source read-only, preserves
   deterministic ordering, and reports the full event count plus bounded
   performance/resilience metrics.
4. the reconnect rehearsal records one successful reconnect, GW004 accepts the
   new BootNotification, and replay continues after the checkpoint.
5. GW004 remains responsive to live/operator processing while the historical
   drain is active; physical display/e-paper verification can be recorded as
   the final hardware observation.
6. `stop` closes the GW001 simulator session cleanly.

If the GW001 hostname is not the desired OCPP charger identity, set
`ARTHEXIS_OCPP_SIMULATOR_IDENTITY` or pass `--charger` to `start`.
