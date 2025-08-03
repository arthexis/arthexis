# OCPP App

This app implements a lightweight Charge Point management system using
[OCPP 1.6](https://github.com/OCA/ocpp) over WebSockets.

### Resetting Migrations

If OCPP migrations become inconsistent, clear and reapply them:

```bash
python manage.py reset_ocpp_migrations
```

### Sink Endpoint

A permissive WebSocket sink is exposed at `ws://127.0.0.1:8000/ws/sink/` which
accepts any OCPP CALL message and replies with an empty CALLRESULT payload. This
is useful when a charge point should be able to send data without triggering
validation errors.

### WebSocket Endpoint

```
ws://127.0.0.1:8000/<path>/<charger_id>/
```

The server accepts connections on any path. The final segment is treated as the
charger ID, so `/CP1/` and `/foo/bar/CP1/` both register charger `CP1`. The full
path used by a charger is stored in the `last_path` field of its database
record.

If a client offers the `ocpp1.6` WebSocket subprotocol, the server will
acknowledge it during the handshake. Clients may also omit the subprotocol and
still connect successfully.

A connected charge point may send standard OCPP CALL messages
(BootNotification, Heartbeat, Authorize, Start/StopTransaction). The
server replies with basic CALLRESULT payloads and records transactions
in the database while keeping active connections in memory. Every charger
known to the system is stored in the `Charger` model. When a device
connects with an unknown ID it will be created automatically. The model
includes a JSON `config` field for storing charger-specific settings.

Each charger also has a `require_rfid` flag that can be enabled to
enforce RFID authentication. When set, the server validates the `idTag`
against entries in the `RFID` table before allowing a transaction to start.

It also records the timestamp of the last `Heartbeat` message and the
payload of the most recent `MeterValues` message received from the charger.
Every individual sampled value is also stored in the `MeterReading` model so
historical meter data can be queried per charger.

Chargers may optionally store their geographic `latitude` and `longitude`.
The admin interface displays a map (centered on Monterrey, Mexico by default)
where these coordinates can be selected by dragging a marker or clicking on the
map.



### REST Endpoints

- `GET /ocpp/chargers/` – list known chargers and their current state.
- `GET /ocpp/chargers/<cid>/` – retrieve details and message log for a charger.
- `POST /ocpp/chargers/<cid>/action/` – send actions such as `remote_stop` or
  `reset` to the charger.
- `GET /ocpp/log/<cid>/` – HTML page showing the message log for a charger.
- `GET /ocpp/` – dashboard listing all chargers and their status.

### Charger Landing Pages

Each `Charger` instance automatically gets a public landing page at
`/ocpp/c/<charger_id>/`. A QR code pointing to this URL is created when the
charger is saved and can be embedded in templates via the `ref_img` tag from the
`references` app. The admin list displays a "Landing Page" link for quick testing.
Another "Log" link opens `/ocpp/log/<charger_id>/` which renders the stored
message exchange as HTML. The landing page lists the charger status, the total
energy delivered so far and a table of recorded sessions with the energy used
in each one.

Active connections remain in-memory via `ocpp.store`. OCPP messages are
also written to the project's log file. Completed charging sessions are
saved in the `Transaction` model for later inspection.

### Simulator

The app includes a small WebSocket charge point simulator located in
`ocpp/simulator.py`.  It can be used to exercise the CSMS during
development.  Example usage:

```python
import asyncio
from ocpp.simulator import SimulatorConfig, ChargePointSimulator

config = SimulatorConfig(host="127.0.0.1", ws_port=8000, cp_path="SIM1/")
sim = ChargePointSimulator(config)
asyncio.run(sim._run_session())
```

The simulator establishes an OCPP 1.6 connection, starts a transaction and
sends periodic meter values.  See the module for additional options such as
RFID authentication or repeat mode.  The `interval` and `kwh_max` parameters
control how often meter values are sent (in seconds) and the total kWh a
simulated session will deliver.

Simulators can also be preconfigured in the Django admin site.  Add
`Simulator` entries and use the provided actions to start or stop them
without writing any code.  The admin list shows the full WebSocket URL for
each simulator along with its configured interval and kWh limit.
