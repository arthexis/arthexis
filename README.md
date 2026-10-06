# Arthexis 3.0

Arthexis is the dashboard and business suite for the Arthexis platform.

Arthexis 3.0 deliberately contains no OCPP protocol or CSMS logic. Charger protocol handling belongs in the separate `ocpp-csms` service; Arthexis consumes business and operational data without becoming the charge-point protocol endpoint.

## Bootstrap

Requires Python 3.11 or newer.

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e .
python manage.py migrate
python manage.py runserver
```

Django admin is available at `/admin/` after creating a superuser:

```bash
python manage.py createsuperuser
```

## Ansible

Ansible is a default project dependency. The initial playbook intentionally does nothing beyond validating that the configured target is reachable:

```bash
ansible-playbook -i ansible/inventory.ini ansible/site.yml
```

Infrastructure responsibilities can grow from this playbook as the deployment model becomes concrete.
