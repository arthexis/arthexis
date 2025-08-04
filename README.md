# Arthexis Constellation

A Django-based MESH-like system. Its objective is to serve as a monorepo that centralizes all the functions and models required by the distinct nodes that compose the execution platform.

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

    The included `runserver` command comes from Daphne via Django Channels,
    so it serves the ASGI application and supports WebSocket endpoints.

   When it starts you will see the server URL along with direct WebSocket
   and admin links printed to the console.

If you prefer an automated setup, run `./install.sh` which creates a
virtual environment and installs dependencies for you.  Adding
`--service <name>` installs a systemd service with the specified name
that launches the server on boot.

   To have the server automatically restart when files change, set
   the `DJANGO_DEV_RELOAD` environment variable:

   ```bash
   DJANGO_DEV_RELOAD=1 python manage.py runserver
   ```

   When the server restarts under VS Code with this variable set, it
   automatically installs any updated dependencies from
   `requirements.txt`, merges and applies migrations, and commits and
   pushes the changes if anything was modified.

The default configuration uses SQLite and is for local development only.
To use PostgreSQL instead, set the `POSTGRES_DB` environment variable (and
optionally `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST` and
`POSTGRES_PORT`) before running management commands. If `POSTGRES_DB` is
defined, the project will connect to a PostgreSQL server using these
settings.

Environment variables can also be placed in files inside the `envs/` directory.
Any `*.env` file found there is automatically loaded when running management
commands or the server. The directory is included in the repository but `.env`
files themselves are ignored so secrets remain local.

### Resetting OCPP Migrations

If OCPP migrations become inconsistent during development, clear their recorded
state and rerun them:
```bash
python manage.py reset_ocpp_migrations
```

This command deletes recorded migration entries for the OCPP app and reapplies
them using Django's migration framework without dropping existing tables.

### Offline Mode

Set the environment variable `ARTHEXIS_OFFLINE=1` to prevent code paths that
require network access from running. Functions marked with a special decorator
will raise an error if they execute while this flag is set, allowing tests or
other strict operations to ensure no external services are contacted.

## VS Code

Launch configurations are provided in `.vscode/launch.json`:

1. **Run Django Server** – starts the site normally without the debugger.
2. **Debug Django Server** – runs the server with debugging enabled.

Open the *Run and Debug* pane in VS Code and choose the desired configuration.

## Internationalization

English is the primary language for this project.  A Spanish translation is
available under `locale/es`.  You can activate it by setting the `LANGUAGE_CODE`
setting or selecting the language via Django's i18n mechanisms.  The supported
languages are defined in `config/settings.py`.

## Logging

Log messages are written to `logs/<active_app>.log` where `<active_app>` is the
application handling the current request. By default this is `website.log`. The
file rotates at midnight with the date appended to the filename. When running
the test suite, logs are stored in `logs/tests.log` instead.

## Updating

Run `./upgrade.sh` to fetch the latest code from this repository. Any
local changes are stashed automatically before pulling and restored
afterwards.  When a virtual environment exists, the script also
reinstalls dependencies.

## Maintaining Documentation

Documentation is split across multiple files. `README.base.md` provides the
overview while each app has its own `README.md` with app-specific details.
After updating any of these files, regenerate `README.md` with:

```bash
python manage.py build_readme
```

Avoid editing the combined `README.md` directly.

## Release

The `release` app provides utilities for publishing the project to PyPI.
Package metadata and optional credentials may be stored in the
`PackageConfig` model which is managed through the Django admin. An admin
action can invoke the full release workflow using the saved settings. Use the
`build_pypi` management command to bump the version, build the distribution and
upload it via Twine:

```bash
python manage.py build_pypi --all
```

Run the command with `--help` to see individual options.

Package metadata lives in the `release.DEFAULT_PACKAGE` dataclass. Provide a
custom `Package` instance or a `Credentials` object to `release.utils.build()` if
you need to override the defaults or supply PyPI credentials programmatically.

## Social Integrations

The `social` app groups functionality for connecting with external social
networks. The `bsky` sub-app links user accounts with the
[Bluesky](https://bsky.app) network, allowing posts on their behalf. The
`meta` sub-app provides basic interaction with Facebook Pages through the Graph
API so content can be published to a page.

## Todos

The `todos` app offers a lightweight API for recording project tasks and a
management command that scans the codebase for `# TODO` comments to populate the
database.

## Subdomain Routing

The project uses Django's **sites** framework together with the `website`
app to select which application handles requests for a given domain.  Each
`Site`'s *name* should be set to the label of the Django app whose `urls`
module will serve that domain.  Requests for unknown domains fall back to
the `readme` site which renders this documentation.


# Readme App

Provides a view for rendering the project's generated `README.md` as HTML
and a management command `build_readme` that rebuilds the file from
`README.base.md` and each app's own `README.md`.


# Nodes App

The `nodes` app exposes a simple JSON interface for keeping track of other instances of this project:

- `POST /nodes/register/` with `hostname`, `address` and optional `port` will register or update the node.
- `GET /nodes/list/` returns all known nodes.
- `GET /nodes/screenshot/` captures a screenshot of the site and records it for the current node.

## NGINX Templates

The `NginxConfig` model manages NGINX templates with support for HTTP, WebSockets,
optional SSL certificates and fallback upstream servers. Templates can be applied
to the host using a management command:

```bash
python manage.py apply_nginx_config <id>
```

The Django admin includes an action to test connectivity to the configured
upstream servers and shows the rendered template for review.

## Recipes

`Recipe` objects allow storing scripts as ordered `Step` entries. In the Django
admin the recipe can be edited either as a list of steps or as a single text
block representing the full script.


# Accounts and Products App

Users may authenticate using any RFID tag assigned to their account. POST the RFID value as JSON to `/accounts/rfid-login/` and the server will return the user's details if the tag matches one stored in the `RFID` model.

The `RFID` model stores card identifiers (8 hexadecimal digits). A tag may belong to a user and is `allowed` by default. Set `allowed` to `false` to disable it.

The `User` model has a **Contact** section containing optional `phone_number` and
`address` fields. The `address` field is a foreign key to the `Address` model
which stores `street`, `number`, `municipality`, `state` and `postal_code`.
Only municipalities from the Mexican states of Coahuila and Nuevo León are
accepted. The user model also includes a `has_charger` flag indicating whether
the user has a charger at that location.

The `RFID` model stores card identifiers (8 hexadecimal digits). A tag may belong to a user or be marked as `blacklisted` to disable it.

## Account Credits

Each user may have an associated **Account** record that tracks available energy credits.
Credits are added (or removed) in the Django admin by creating **Credit** entries.
Each entry stores the amount, who created it and when it was added so every
movement is tracked individually. Consumption is calculated from recorded
transactions. The account exposes:

- `credits_kwh` – sum of all credit amounts.
- `total_kwh_spent` – kWh consumed across transactions.
- `balance_kwh` – remaining credit after subtracting usage.

The account is linked to the user with a one‑to‑one relationship and can be
referenced during authorization or billing steps. Accounts include a **Service
Account** flag which, when enabled, bypasses balance checks during
authorization. The admin lists the current authorization status so staff can
quickly verify whether an account would be accepted by a charger.

## Vehicles

An account may be associated with multiple **Vehicle** records. Each vehicle
stores the `brand`, `model` and `VIN` (Vehicle Identification Number) so that a
user's cars can be identified when using OCPP chargers.

## Products and Subscriptions

Provides a simple subscription model:

- `GET /accounts/products/` returns available products.
- `POST /accounts/subscribe/` with `account_id` and `product_id` creates a subscription.
- `GET /accounts/list/?account_id=<id>` lists subscriptions for an account.

## RFID CSV Utilities

RFID tags can be exported and imported using management commands:

- `python manage.py export_rfids [path]` writes all tags to CSV. If `path` is omitted the data is printed to stdout.
- `python manage.py import_rfids path` loads tags from a CSV file created by the export command.
- The Django admin also provides export and import actions powered by [`django-import-export`](https://django-import-export.readthedocs.io/).


# OCPP App

This app implements a lightweight Charge Point management system using
[OCPP 1.6](https://github.com/OCA/ocpp) over WebSockets.

### Resetting Migrations

If OCPP migrations become inconsistent, clear their recorded state and rerun
them:


```bash
python manage.py reset_ocpp_migrations
```

This deletes recorded migration entries for the OCPP app and reapplies them
without dropping existing tables.

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


# References App

Provides a small `Reference` model that stores values or links which can be represented by QR codes or other methods. Each
reference can store alternative text and tracks how often it is used. A template tag `ref_img` renders the QR image in templates
and automatically creates or updates the record when needed.

A simple landing page at `/ref/` can generate a QR code for arbitrary text without saving anything to the database.



# AWG App

Provides reference tables for American Wire Gauge calculations. Two models store cable specifications and conduit fill allowances.

## AWG Calculator

A landing page is available at `/awg/awg-calculator/` that lets you select an
AWG size, material, and number of conductors to view properties such as
diameter, area, resistance, ampacity and the conduit fill table.


# Release App

Provides utilities for packaging the project and uploading it to PyPI.

Package metadata and PyPI credentials are represented by simple dataclasses. The
`DEFAULT_PACKAGE` constant exposes the current project details while the
`Credentials` class can hold either an API token or a username/password pair for
Twine uploads.

For convenience the `PackageConfig` model stores this information in the
database. It is exposed in the Django admin where an action can invoke the
release workflow using the stored metadata and credentials.

The management command `build_pypi` wraps the release logic. Run it with `--all`
for the full workflow:

```bash
python manage.py build_pypi --all
```

Individual flags exist for incrementing the version, building the distribution
and uploading via Twine. See `--help` for details.


# CRM App

Provides customer relationship management features, including an integration with an Odoo server.
The `crm.odoo` sub-app stores connection credentials in the `Instance` model and exposes a
`/odoo/test/<id>/` endpoint to verify authentication. Instances can be managed through the
Django admin under **Relationship Managers**, where a **Test connection** action attempts to
authenticate with the selected servers.


# Footer App

Collects and exposes links displayed at the bottom of the site.
Views can be decorated with `footer_link()` to appear in the footer.
Links can be grouped into columns by providing a column name to the decorator.


# Todos

Simple app for tracking tasks within the project.

## API

- `GET /todos/` – list all todos.
- `POST /todos/` – create a new todo. JSON body: `{ "text": "Buy milk" }`
- `POST /todos/<id>/toggle/` – toggle completion status.

## Importing from Code

Run the management command to create todo items from `# TODO` comments found in
project files:

```bash
python manage.py import_todos
```


# Website App

Displays the README for a particular app depending on the subdomain.
The mapping uses Django's `Site` model: set a site's *name* to the
label of the app whose README should be shown. If the domain isn't
recognized, the project README is rendered instead.

Rendered pages use [Bootstrap](https://getbootstrap.com/) loaded from a CDN so
the README content has simple default styling. The JavaScript bundle is also
included so interactive components like the navigation dropdowns work. A button
in the upper-right corner toggles between light and dark themes and remembers
the preference using `localStorage`.

When visiting the default *website* domain, a navigation bar shows links to all
enabled apps that expose public URLs. Views decorated with `footer_link` are
collected into a footer where links can be grouped into columns. The
automatically generated sitemap now appears there. The footer stays at the
bottom of short pages but scrolls into view on longer ones.


# Clipboard

Stores clipboard text snippets as `Sample` entries. Use the management command
`sample_clipboard` or the Django admin to capture the current system clipboard
content.

Patterns can be defined with optional `[sigils]` to scan the most recent sample.
Each pattern has a `priority` and the admin action **Scan latest sample**
returns the first matching pattern along with any sigil substitutions.

