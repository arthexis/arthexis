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
in memory.

### REST Endpoints

- `GET /ocpp/chargers/` – list known chargers and their current state.
- `GET /ocpp/chargers/<cid>/` – retrieve details and message log for a charger.
- `POST /ocpp/chargers/<cid>/action/` – send actions such as `remote_stop` or
  `reset` to the charger.

Data is stored in the in-memory module `ocpp.store` and is not persisted.
