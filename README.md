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

   To have the server automatically restart when files change, set
   the `DJANGO_DEV_RELOAD` environment variable:

   ```bash
   DJANGO_DEV_RELOAD=1 python manage.py runserver
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

## Subdomain Routing

The project uses Django's **sites** framework together with the `website`
app to select which application handles requests for a given domain.  Each
`Site`'s *name* should be set to the label of the Django app whose `urls`
module will serve that domain.  Requests for unknown domains fall back to
the `readme` site which renders this documentation.


# Chat App

This project includes basic websocket support using [Django Channels](https://channels.readthedocs.io/). After launching the development server you can connect a websocket client to `ws://localhost:8000/ws/echo/` and any text you send will be echoed back.


# Nodes App

The `nodes` app exposes a simple JSON interface for keeping track of other instances of this project:

- `POST /nodes/register/` with `hostname`, `address` and optional `port` will register or update the node.
- `GET /nodes/list/` returns all known nodes.


# Accounts App

Users may authenticate using any RFID tag assigned to their account. POST the RFID value as JSON to `/accounts/rfid-login/` and the server will return the user's details if the tag matches one stored in the `RFID` model.

The `User` model includes an optional `phone_number` field for storing a contact phone number.

The `RFID` model stores card identifiers (8 hexadecimal digits). A tag may belong to a user or be marked as `blacklisted` to disable it.

## Account Credits

Each user may have an associated **Account** record that tracks available energy credits. The model stores:

- `credits_kwh` – total kWh purchased or granted to the user.
- `total_kwh_spent` – kWh consumed so far.
- `balance_kwh` – property returning the remaining credit.

The account is linked to the user with a one‑to‑one relationship and can be referenced during authorization or billing steps.

## Vehicles

An account may be associated with multiple **Vehicle** records. Each vehicle
stores the `brand`, `model` and `vin` (Vehicle Identification Number) so that a
user's cars can be identified when using OCPP chargers.


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

Each charger also has a `require_rfid` flag that can be enabled to
enforce RFID authentication. When set, the server validates the `idTag`
against entries in the `RFID` table before allowing a transaction to start.

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

### Simulator

The app includes a small WebSocket charge point simulator located in
`ocpp/simulator.py`.  It can be used to exercise the CSMS during
development.  Example usage:

```python
import asyncio
from ocpp.simulator import SimulatorConfig, ChargePointSimulator

config = SimulatorConfig(host="localhost", ws_port=8000, cp_path="SIM1")
sim = ChargePointSimulator(config)
asyncio.run(sim._run_session())
```

The simulator establishes an OCPP 1.6 connection, starts a transaction and
sends periodic meter values.  See the module for additional options such as
RFID authentication or repeat mode.


# Odoo App

Provides a simple integration with an Odoo server. The `Instance` model stores
connection credentials and a `/odoo/test/<id>/` endpoint checks whether the
specified instance can be authenticated.


# Readme App

Provides a view for rendering the project's generated `README.md` as HTML
and a management command `build_readme` that rebuilds the file from
`README.base.md` and each app's own `README.md`.


# Website App

Displays the README for a particular app depending on the subdomain.
The mapping uses Django's `Site` model: set a site's *name* to the
label of the app whose README should be shown. If the domain isn't
recognized, the project README is rendered instead.

Rendered pages use [Bootstrap](https://getbootstrap.com/) loaded from a CDN so
the README content has simple default styling.
