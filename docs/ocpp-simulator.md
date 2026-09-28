# Live OCPP charge-point simulator

Arthexis includes a model-independent OCPP 1.6J client for exercising a real
CSMS over the network. The live simulator represents the charger. It must not
reach into the target satellite's database, Redis, Celery, or event bus.

The operator surface is the `ocpp_simulator` Django management command. Gway
can ingest that command in the same way as other Arthexis management commands.

## GWAY-001 → GW004 field test

On GWAY-001, install the same Arthexis build used for the simulator and open a
connection to the GW004 OCPP listener. For a trusted isolated Ethernet test
network, plaintext WebSocket must be opted into explicitly:

```console
python manage.py ocpp_simulator open \
  --url ws://192.168.129.10:9000 \
  --charger GWAY001 \
  --allow-insecure-ws
```

`open` establishes the WebSocket, negotiates `ocpp1.6`, sends
`BootNotification`, requires an `Accepted` boot result, and leaves a local
worker owning that same connection. Production-style endpoints should use
`wss://`; insecure `ws://` is rejected unless explicitly allowed.

Send an arbitrary RFID/idTag without predicting the authorization result:

```console
python manage.py ocpp_simulator authorize \
  --charger GWAY001 \
  --id-tag TEST001
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
python manage.py ocpp_simulator status --charger GWAY001
python manage.py ocpp_simulator reconnect --charger GWAY001
python manage.py ocpp_simulator close --charger GWAY001
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
python manage.py ocpp_simulator replay \
  --charger GWAY001 \
  --source /path/to/reconciled.sqlite3 \
  --source-charger FIELD_CHARGER \
  --stream transactions \
  --pacing maximum
```

To reproduce an interrupted drain, reconnect after a fixed number of delivered
events. The simulator closes the live WebSocket, opens a new one for the same
charger identity, performs `BootNotification`, requires `Accepted`, and then
continues the same replay stream:

```console
python manage.py ocpp_simulator replay \
  --charger GWAY001 \
  --source /path/to/reconciled.sqlite3 \
  --source-charger FIELD_CHARGER \
  --stream transactions \
  --reconnect-after 500
```

Historical payload timestamps remain intact where retained by the replay source;
delivery timing is independent. Use `--pacing fixed --interval-seconds ...`
for a fixed rate, or `--pacing burst --burst-size ... --burst-pause-seconds ...`
for burst delivery. `--batch-size` controls SQLite fetch size rather than
loading the complete history into memory.

When the migrated database contains retained charger-originated
`InboundProtocolRequest` payloads, replay those protocol-realistic requests
directly instead of reconstructing transaction history:

```console
python manage.py ocpp_simulator replay \
  --charger GWAY001 \
  --source /path/to/reconciled.sqlite3 \
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
