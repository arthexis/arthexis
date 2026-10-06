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

Ansible is a default project dependency. The Arthexis-only playbook currently does nothing beyond validating that the configured target is reachable:

```bash
ansible-playbook -i ansible/inventory.ini ansible/site.yml
```

### Appliance composition

Arthexis owns composition of the complete appliance, while each repository continues to own its own deployment implementation. `ansible/appliance.yml` first runs the Arthexis playbook and then invokes the OCPP-CSMS-owned `ansible/playbooks/satellite.yml` from inside the OCPP-CSMS checkout. This preserves the OCPP-CSMS repository's own `ansible.cfg`, roles, inventory defaults, and deployment behavior.

With the repositories checked out as siblings:

```text
Repos/
├── arthexis/
└── ocpp-csms/
```

run:

```bash
ansible-playbook -i ansible/inventory.ini ansible/appliance.yml
```

The sibling `../ocpp-csms` checkout is the default. Override it either with an extra variable:

```bash
ansible-playbook -i ansible/inventory.ini ansible/appliance.yml \
  -e ocpp_csms_repo=/path/to/ocpp-csms
```

or with the `OCPP_CSMS_REPO` environment variable.

OCPP-CSMS keeps control of its own inventory. Its default field inventory is empty, so appliance composition does not target a field host unless one is supplied. To choose an OCPP-CSMS inventory explicitly, set `OCPP_CSMS_INVENTORY` or pass `ocpp_csms_inventory`:

```bash
ansible-playbook -i ansible/inventory.ini ansible/appliance.yml \
  -e ocpp_csms_inventory=ansible/inventory/my-host.yml
```

The composition boundary is intentionally narrow: Arthexis decides that OCPP-CSMS should be deployed, while OCPP-CSMS decides how it is deployed.
