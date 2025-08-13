# The Constellation

## Purpose
Arthexis Constellation is a Django-based suite that centralizes tools for managing charging infrastructure and related services.

## Setup
1. `pip install -r requirements.txt`
2. `python manage.py migrate`
3. `python manage.py runserver`
4. Run management commands with `python manage.py <command>`

## Included Apps
| App | Purpose |
| --- | --- |
| accounts | User accounts, RFID login, and credit management |
| app | Core utilities and site model tweaks |
| awg | American Wire Gauge references and calculator |
| clipboard | Store and scan clipboard text samples |
| crm | Customer relationship management with Odoo support |
| emails | Email templates and messaging |
| nodes | Register project nodes and manage NGINX templates |
| ocpp | OCPP 1.6 charge point management |
| references | Reusable references and QR codes |
| release | Packaging and PyPI release helpers |
| rfid | RFID tag model and helpers |
| social.bsky | Bluesky integration |
| social.meta | Facebook page integration |
| todos | Track project tasks from code TODOs |
| website | Default site and README renderer |
| readme | Build project README and expose docs |

## External Links
- [Python 3.12](https://www.python.org/downloads/release/python-31210/)
- [MIT License](LICENSE)
- [Django](https://www.djangoproject.com/)
- [Channels](https://channels.readthedocs.io/)
- [Celery](https://docs.celeryq.dev/)
- [Bootstrap](https://getbootstrap.com/)

---

*Note: do not modify this README unless directed. Use Django's admindocs for app documentation.*
