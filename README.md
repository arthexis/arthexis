# Arthexis 3.0

Arthexis is the dashboard and business suite for the Arthexis platform.

Arthexis 3.0 deliberately contains no OCPP protocol or CSMS logic. Charger protocol handling belongs in the separate `ocpp-csms` service; Arthexis consumes business and operational data without becoming the charge-point protocol endpoint.

## UI stack

Arthexis uses a server-rendered Django UI rather than a separate SPA:

- Django templates for pages and reusable fragments
- HTMX for polling, partial updates, filters, and server-driven interactions
- Alpine.js for small local browser interactions
- Tailwind CSS for the visual system
- Apache ECharts for charger, energy, utilization, and business visualizations

The initial dashboard is available at `/`. The current charger and energy values are illustrative placeholders until the business and OCPP-CSMS integration models are added.

The browser libraries are CDN-backed during the bootstrap phase to keep the project free of a Node/npm build requirement. They can be vendored or compiled later without changing the server-rendered architecture.

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

Arthexis owns the central Watchtower deployment. Component playbooks live under
`ansible/playbooks/`:

- `arthexis.yml` — Arthexis application component
- `ocpp-collector.yml` — OCPP Collector component
- `watchtower.yml` — combined Watchtower deployment

Run the normal central deployment with:

```bash
ansible-playbook ansible/playbooks/watchtower.yml
```

OCPP component naming is intentionally consistent across repositories and
deployment surfaces: OCPP CSMS, OCPP Forwarder, and OCPP Collector use the
`ocpp_` prefix for Python/Ansible identifiers and `ocpp-` for service and
playbook filenames.

### Appliance composition

The older appliance composition remains available for development/integration.
`ansible/appliance.yml` runs the Arthexis playbook and then invokes the
OCPP-CSMS-owned `ansible/playbooks/satellite.yml` from inside the OCPP-CSMS
checkout. This preserves the OCPP-CSMS repository's own `ansible.cfg`, roles,
inventory defaults, and deployment behavior.

With the repositories checked out as siblings:

```text
Repos/
├── arthexis/
└── ocpp-csms/
```

run:

```bash
ansible-playbook ansible/appliance.yml
```

The sibling `../ocpp-csms` checkout is the default. Override it either with an extra variable:

```bash
ansible-playbook ansible/appliance.yml \
  -e ocpp_csms_repo=/path/to/ocpp-csms
```

or with the `OCPP_CSMS_REPO` environment variable.

OCPP-CSMS keeps control of its own inventory. Its default field inventory is empty, so appliance composition does not target a field host unless one is supplied. To choose an OCPP-CSMS inventory explicitly, set `OCPP_CSMS_INVENTORY` or pass `ocpp_csms_inventory`:

```bash
ansible-playbook ansible/appliance.yml \
  -e ocpp_csms_inventory=ansible/inventory/my-host.yml
```

The composition boundary is intentionally narrow: Arthexis decides that OCPP-CSMS should be deployed, while OCPP-CSMS decides how it is deployed.
