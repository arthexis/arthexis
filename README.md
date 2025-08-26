# Arthexis Constellation

## Purpose
Arthexis Constellation is a Django-based suite that centralizes tools for managing charging infrastructure and related services.

## Installation
1. Clone the repository: `git clone <repository_url>`
2. Change into the project directory: `cd arthexis`
3. *(Optional)* Create and activate a virtual environment: `python -m venv .venv` and `source .venv/bin/activate`
4. Install dependencies: `pip install -r requirements.txt`

## Shell Scripts
The project includes helper shell scripts to streamline development. Each script's supported flags are listed below:

- `install.sh`  \
  Set up a virtual environment, install dependencies, and optionally configure a systemd service. Flags:
  - `--service NAME` – install a systemd service with the given name.
  - `--nginx` – install an nginx proxy (defaults to internal mode).
  - `--public`, `--internal` – specify nginx mode; also sets the default port (`8000` or `8888`).
  - `--port PORT` – run on a custom port.
  - `--auto-upgrade` – enable periodic upgrade checks.
  - `--latest` – track the latest commit when auto-upgrading.
  - `--celery` – enable Celery worker and beat.
  - `--satellite` – shortcut for internal nginx service with auto-upgrade, latest, and Celery.

- `start.sh`  \
  Run the development server. Flags:
  - `--port PORT` – serve on a specific port (defaults to `8888`, or `8000` with `--public`).
  - `--reload` – enable auto-reload.
  - `--celery`, `--no-celery` – start or skip Celery worker and beat.
  - `--public`, `--internal` – shorthands for port `8000` or `8888`.

- `stop.sh`  \
  Stop a running development server. Flags:
  - `[PORT]` – stop the server on the given port (default `8888`).
  - `--all` – stop all running servers.

- `command.sh <command> [args...]`  \
  Execute Django management commands using hyphenated names; no additional flags.

- `dev-maintenance.sh`  \
  Install updated dependencies when requirements change and perform database maintenance tasks; no flags.

- `upgrade.sh`  \
  Pull the latest code and reinstall dependencies when needed. Flags:
  - `--latest` – force upgrade to the latest commit.
  - `--clean-db` – remove the existing database before upgrading.
  - `--no-restart` – skip restarting the server after upgrade.

## VS Code Tasks
The `.vscode/tasks.json` file provides two tasks:

- **Dev: maintenance** – runs `dev-maintenance.sh` (or the Windows `.bat` equivalent).
- **Update requirements** – installs updated dependencies via `install.sh` and regenerates `requirements.txt`.

## Public Site Applications
Only applications with site fixtures and public views are listed below.

| App | Purpose and notable views |
| --- | --- |
| rfid | RFID tag reader with scan, restart and test endpoints |
| ocpp | OCPP 1.6 charge point dashboard, charger status and log pages |
| refs | Recent references list and on‑the‑fly QR generator |
| awg | AWG wire gauge calculator and conduit fill references |
| arts | Article viewer with calendar and navigation |

## External Links
- [Python 3.12](https://www.python.org/downloads/release/python-31210/)
- [MIT License](LICENSE)
- [Django](https://www.djangoproject.com/)
- [Channels](https://channels.readthedocs.io/)
- [Celery](https://docs.celeryq.dev/)
- [Bootstrap](https://getbootstrap.com/)
