# Arthexis Constellation

## Purpose
Arthexis Constellation is a Django-based suite that centralizes tools for managing charging infrastructure and related services.

## Installation
1. Clone the repository: `git clone <repository_url>`
2. Change into the project directory: `cd arthexis`
3. *(Optional)* Create and activate a virtual environment: `python -m venv .venv` and `source .venv/bin/activate`
4. Install dependencies: `pip install -r requirements.txt`

## Shell Scripts
The project includes helper shell scripts to streamline development:

- `install.sh`  \
  Set up a virtual environment, install dependencies, and optionally configure a systemd service with `--service NAME`.

- `start.sh [port]`  \
  Run the development server on the specified port (default `8888`).

- `stop.sh [port|--all]`  \
  Stop the server on a given port or all running servers.

- `command.sh <command> [args...]`  \
  Execute Django management commands using hyphenated names (e.g., `./command.sh show-migrations`).

- `dev-maintenance.sh`  \
  Install updated dependencies when requirements change and perform database maintenance tasks.

- `upgrade.sh`  \
  Pull the latest code and reinstall dependencies when needed.

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
