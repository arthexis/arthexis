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

### Development Maintenance

Running `dev-maintenance.bat` (or `./dev-maintenance.sh`) installs dependencies,
removes the SQLite database (default `db.sqlite3` or the path from `DB_PATH`),
and reruns migrations. This resets the database automatically for a clean
development setup.

### Resetting OCPP Migrations

If OCPP migrations become inconsistent during development, clear their recorded
state and rerun them:

```bash
python manage.py reset_ocpp_migrations
```

This command deletes recorded migration entries for the OCPP app and reapplies
them with Django's `--fake-initial` option so existing tables remain untouched.

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
The combined `README.md` is generated during the release process.
Avoid editing the combined `README.md` directly.

## Release

The `release` app provides utilities for publishing the project to PyPI.
Package metadata and optional credentials may be stored in the
`PackageConfig` model which is managed through the Django admin. An admin
action can invoke the full release workflow using the saved settings. The
`build_pypi` management command regenerates the project `README.md`, bumps the
version, builds the distribution and optionally uploads it via Twine:

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
