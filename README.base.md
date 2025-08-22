# Arthexis Constellation

## Purpose
Arthexis Constellation is a Django-based suite that centralizes tools for managing charging infrastructure and related services.

## Installation
1. Clone the repository: `git clone <repository_url>`
2. Change into the project directory: `cd arthexis`
3. *(Optional)* Create and activate a virtual environment: `python -m venv .venv` and `source .venv/bin/activate`
4. Install dependencies: `pip install -r requirements.txt`

## Included Apps
| App | Purpose |
| --- | --- |
| accounts | User accounts, RFID login, and credit management |
| app | Core utilities and site model tweaks |
| awg | American Wire Gauge references and calculator |
| emails | Email templates and messaging |
| integrator | Integrations with external services like Bluesky, Facebook, and Odoo |
| nodes | Register project nodes and manage NGINX templates |
| ocpp | OCPP 1.6 charge point management |
| refs | Reusable references and QR codes |
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
