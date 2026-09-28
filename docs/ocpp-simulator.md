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
