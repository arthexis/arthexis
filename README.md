# Arthexis Constellation

## Purpose
Arthexis Constellation is a Django-based suite that centralizes tools for managing charging infrastructure and related services.

## Installation
1. Clone the repository: `git clone <repository_url>`
2. Change into the project directory: `cd arthexis`
3. *(Optional)* Create and activate a virtual environment: `python -m venv .venv` and `source .venv/bin/activate`
4. Install dependencies: `pip install -r requirements.txt`

## Setup
1. `python manage.py migrate`
2. `python manage.py runserver`
3. Run management commands with `python manage.py <command>`

## Shell Scripts
The project includes helper shell scripts to streamline development:
- `install.sh` – set up a virtual environment, install dependencies, and optionally configure a systemd service with `--service NAME`.
- `start.sh [port]` – run the development server on the specified port (default `8888`).
- `stop.sh [port|--all]` – stop the server on a given port or all running servers.
- `manage.sh <args>` – execute Django management commands.
- `command.sh <command> [args...]` – run management commands using hyphenated names (e.g., `./command.sh show-migrations`).
- `dev-maintenance.sh` – install updated dependencies when requirements change and perform database maintenance tasks.
- `upgrade.sh` – pull the latest code and reinstall dependencies when needed.

## VS Code Tasks
Two tasks are provided in `.vscode/tasks.json`:
- **Dev: maintenance** – runs `dev-maintenance.sh` (or the Windows `.bat` equivalent).
- **Update requirements** – installs updated dependencies via `install.sh` and regenerates `requirements.txt`.

## Included Apps
| App | Purpose |
| --- | --- |
| accounts | User accounts, RFID login, and credit management |
| app | Core utilities and site model tweaks |
| awg | American Wire Gauge references and calculator |
| emails | Email templates and messaging |
| integrations | Integrations with external services like Bluesky, Facebook, and Odoo |
| nodes | Register project nodes and manage NGINX templates |
| ocpp | OCPP 1.6 charge point management |
| references | Reusable references and QR codes |
| release | Packaging and PyPI release helpers |
| rfid | RFID tag model and helpers |
| website | Default site and README renderer |

## External Links
- [Python 3.12](https://www.python.org/downloads/release/python-31210/)
- [MIT License](LICENSE)
- [Django](https://www.djangoproject.com/)
- [Channels](https://channels.readthedocs.io/)
- [Celery](https://docs.celeryq.dev/)
- [Bootstrap](https://getbootstrap.com/)
