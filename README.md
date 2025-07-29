# Arthexis Django Project

This repository contains a basic [Django](https://www.djangoproject.com/) project.

## Setup

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Apply database migrations:
   ```bash
   python manage.py migrate
   ```
3. Run the development server:
   ```bash
   python manage.py runserver
   ```

The default configuration uses SQLite and is for local development only.
To use PostgreSQL instead, set the `POSTGRES_DB` environment variable (and
optionally `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST` and
`POSTGRES_PORT`) before running management commands. If `POSTGRES_DB` is
defined, the project will connect to a PostgreSQL server using these
settings.

## VS Code

Launch configurations are provided in `.vscode/launch.json`:

1. **Run Django Server** – starts the site normally without the debugger.
2. **Debug Django Server** – runs the server with debugging enabled.

Open the *Run and Debug* pane in VS Code and choose the desired configuration.

## Maintaining Documentation

Documentation is split across multiple files. `README.base.md` provides the
overview while each app has its own `README.md` with app-specific details.
After updating any of these files, regenerate `README.md` with:

```bash
python manage.py build_readme
```

Avoid editing the combined `README.md` directly.


# Chat App

This project includes basic websocket support using [Django Channels](https://channels.readthedocs.io/). After launching the development server you can connect a websocket client to `ws://localhost:8000/ws/echo/` and any text you send will be echoed back.


# Nodes App

The `nodes` app exposes a simple JSON interface for keeping track of other instances of this project:

- `POST /nodes/register/` with `hostname`, `address` and optional `port` will register or update the node.
- `GET /nodes/list/` returns all known nodes.


# Accounts App

Users may authenticate using the UID of an RFID card. POST the UID as JSON to `/accounts/rfid-login/` and the server will return the user's details if the UID matches an existing account.


# Subscriptions App

Provides a simple subscription model:

- `GET /subscriptions/products/` returns available products.
- `POST /subscriptions/subscribe/` with `user_id` and `product_id` creates a subscription.
- `GET /subscriptions/list/?user_id=<id>` lists subscriptions for a user.


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
