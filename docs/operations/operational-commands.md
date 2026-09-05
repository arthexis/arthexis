# Operational commands via `command.sh` / `command.bat`

`command.sh` (POSIX) and `command.bat` (Windows) now expose an explicit allowlist of operational one-word command entrypoints ("ops commands").

For advanced admin workflows and any non-allowlisted Django command, run `manage.py` directly.

## Usage

```bash
./command.sh list
./command.sh <command> [args...]
./manage.py <django-command> [args...]
```

## Supported operational commands

- `admin`
- `analytics`
- `availability`
- `benchmark`
- `browse`
- `changelog`
- `channels`
- `charger`
- `chargers`
- `coverage`
- `create`
- `diagnose`
- `doctor`
- `email`
- `env`
- `estimate`
- `feature`
- `features`
- `fixtures`
- `github`
- `godaddy`
- `good`
- `groups`
- `health`
- `https`
- `imager`
- `invite`
- `message`
- `migrations`
- `nginx`
- `node`
- `notify`
- `ocpp`
- `password`
- `redis`
- `release`
- `repo`
- `rfid`
- `run_release_data_transforms`
- `runserver`
- `sensors`
- `startup`
- `test`
- `upgrade`
- `uptime`
- `utils`

## Notes for AGENTS and operators

When an operation explicitly asks for an **ops command**, use `command.sh` / `command.bat` with one of the allowlisted names above.

For everything else, including Django built-ins like `makemigrations`, `shell`,
and direct test targeting, use `manage.py` directly.

### Benchmarking command references

For benchmarking workflows, treat command help text as the canonical documentation source:

- `scripts/benchmark-suite.sh --help`
- `.venv/bin/python manage.py benchmark_ocpp_memory --help`
- `.venv/bin/python manage.py migrations benchmark --help`
- `.venv/bin/python manage.py test benchmark --help`

Keep long-form benchmark guidance anchored to these help outputs rather than a standalone benchmarking page.

### RFID command-card templates

RFID command-card templates can be listed and written with the RFID command:

```bash
./command.sh rfid command-card --list-commands
./command.sh rfid command-card --write-command "HEALTH SNAPSHOT"
./command.sh rfid command-card burn --template "HEALTH SNAPSHOT"
./command.sh rfid command-card burn
```

The burn command uses the selected template when one is provided. Without a
template it copies the latest scanned command card, skipping the burner card
itself.
