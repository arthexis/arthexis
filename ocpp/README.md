# OCPP App

This app implements a lightweight Charge Point management system using
[OCPP 1.6](https://github.com/OCA/ocpp) over WebSockets.

### WebSocket Endpoint

```
ws://<host>/ws/ocpp/<charger_id>/
```

A connected charge point may send standard OCPP CALL messages
(BootNotification, Heartbeat, Authorize, Start/StopTransaction). The
server replies with basic CALLRESULT payloads and records transactions
in the database while keeping active connections in memory. Every charger
known to the system is stored in the `Charger` model. When a device
connects with an unknown ID it will be created automatically. The model
includes a JSON `config` field for storing charger-specific settings.

Each charger also has a `require_rfid` flag that can be enabled to
enforce RFID authentication. When set, the server validates the `idTag`
against known users before allowing a transaction to start.

It also records the timestamp of the last `Heartbeat` message and the
payload of the most recent `MeterValues` message received from the charger.



### REST Endpoints

- `GET /ocpp/chargers/` – list known chargers and their current state.
- `GET /ocpp/chargers/<cid>/` – retrieve details and message log for a charger.
- `POST /ocpp/chargers/<cid>/action/` – send actions such as `remote_stop` or
  `reset` to the charger.

Active connections and logs remain in-memory via `ocpp.store`, but
completed charging sessions are saved in the `Transaction` model for
later inspection.
