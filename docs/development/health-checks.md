# Health checks

`manage.py health` is the central interface for operational checks.

## Usage

Run individual targets:

```bash
python manage.py health --target core.time
python manage.py health --target ocpp.forwarders
```

Run grouped checks:

```bash
python manage.py health --group core
python manage.py health --all
```

List available checks:

```bash
python manage.py health --list-targets
```

## Exit codes

- `0`: all checks passed
- `1`: one or more checks failed
- `2`: invalid selection (unknown target/group or no resolved checks)

## Naming and structure for new checks

- Target format is `<app_label>.<check_name>`.
- Group names use app labels (for example `core`, `ocpp`, `release`).
- Reusable implementations live in service modules:
  - `apps/<app_label>/services/health_checks.py`
- Management-command wrappers remain as `check_*` commands for compatibility and should:
  - emit a deprecation warning
  - delegate to `manage.py health --target <app_label>.<check_name>`

## Current inventory

### Core

- `check_admin` → `core.admin`
- `check_lcd_send` → `core.lcd_send`
- `check_lcd_service` → `core.lcd_service`
- `check_next_upgrade` → `core.next_upgrade`
- `check_rfid` → `core.rfid`
- `check_system_user` → `core.system_user`
- `check_time` → `core.time`

### OCPP

- `check_forwarders` → `ocpp.forwarders`

### Release

- `check_pypi` → `release.pypi`
