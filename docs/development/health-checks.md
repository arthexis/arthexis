# Health checks

`manage.py health` is the central interface for operational checks.

## Usage

Run individual targets:

```bash
.venv/bin/python manage.py health --target core.time
.venv/bin/python manage.py health --target ocpp.forwarders
```

Run grouped checks:

```bash
.venv/bin/python manage.py health --group core
.venv/bin/python manage.py health --all
```

List available checks:

```bash
.venv/bin/python manage.py health --list-targets
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
- Legacy `check_*` compatibility wrappers were removed; invoke `manage.py health` targets directly.
- Group execution only includes targets marked `grouped=yes` in `--list-targets` output (controlled by `include_in_group` in `HealthCheckDefinition`).

## Current inventory

### Core

- `core.admin`
- `core.lcd_send`
- `core.lcd_service`
- `core.next_upgrade`
- `core.rfid`
- `core.system_user`
- `core.time`

### OCPP

- `ocpp.forwarders`

### Release

- `release.pypi`
