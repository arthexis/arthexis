# Node Role Playbook Configuration Profiles

Release engineering can now manage Ansible metadata for each node role directly in Django admin. Each role exposes a **Role Configuration Profile** inline on the node role change page (`/admin/nodes/noderole/`). The profile stores:

- `ansible_playbook_path` – the repository-relative playbook executed for this role (defaults to `ansible/playbooks/<role>.yml`).
- `inventory_group` – the Ansible inventory group name associated with the role.
- `extra_vars` – JSON payload mirroring the shell defaults enforced by `switch-role.sh` (Celery, Redis, LCD flags, nginx mode, etc.).
- `default_tags` – optional JSON array describing the default tag set to run for the role.

Before switching a node into the **Control** or **Satellite** role (whether through `switch-role.sh` or by applying the associated playbook profile), verify that the host runs Ubuntu 22.04 or newer and exposes an `eth0` network interface. These baselines ensure the role playbooks can bind to predictable networking without additional overrides.

## Current Seed Values

Existing roles were backfilled during migration `0042_roleconfigurationprofile` using the flags hard-coded in `switch-role.sh`:

| Role       | Playbook Path                         | Inventory Group | Extra Vars snapshot |
|------------|---------------------------------------|-----------------|---------------------|
| Terminal   | `ansible/playbooks/terminal.yml`      | `terminal`      | `{"enable_celery": true, "requires_redis": false, "enable_lcd_screen": false, "enable_control": false, "nginx_mode": "internal"}` |
| Control    | `ansible/playbooks/control.yml`       | `control`       | `{"enable_celery": true, "requires_redis": true, "enable_lcd_screen": true, "enable_control": true, "nginx_mode": "internal"}` |
| Watchtower | `ansible/playbooks/watchtower.yml`    | `watchtower`    | `{"enable_celery": true, "requires_redis": true, "enable_lcd_screen": false, "enable_control": false, "nginx_mode": "public"}` |
| Satellite  | `ansible/playbooks/satellite.yml`     | `satellite`     | `{"enable_celery": true, "requires_redis": true, "enable_lcd_screen": false, "enable_control": false, "nginx_mode": "internal"}` |

The defaults above provide parity with the script behaviour. Platform operations should adjust these values if playbook locations or role flags diverge from the legacy shell tooling.

## Updating Playbook Mappings

1. Open **Nodes → Node Roles** in Django admin and choose the role to adjust.
2. Locate the **Role configuration profile** inline form at the bottom of the page.
3. Update the playbook path, inventory group, JSON fields, or default tag list as needed.
4. Click **Save** to persist the changes. The inline will validate JSON content before committing updates.

Remember to coordinate any inventory/tag changes with deployment tooling so automation jobs remain aligned with node role expectations.
