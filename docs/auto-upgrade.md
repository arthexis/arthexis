# Auto-Upgrade and Delegated Upgrade Flow

This document describes how nodes upgrade themselves through Celery, how the
delegated systemd unit is launched, and what to check if something fails.

## Prerequisites

- `./env-refresh.sh` has been run so `/usr/local/bin/watch-upgrade` exists and is
  executable.
- `.locks/service.lck` contains the managed service name (for example
  `arthexis`) so the watcher knows which unit to stop and restart.
- The service user can run `systemd-run` (with passwordless sudo when required).
- `upgrade.sh` remains executable in the project root.
- The Celery beat schedule is kept in sync with `.locks/auto_upgrade.lck`; when
  the lock is removed, the periodic task is removed as well, and any
  environment override set with `ARTHEXIS_UPGRADE_FREQ` is ignored unless it is
  a positive integer.

## How delegation works

1. Celery calls `scripts/delegated-upgrade.sh` when an update is required.
2. `scripts/delegated-upgrade.sh` launches a transient unit with `systemd-run`, setting:
   - `WorkingDirectory` to the project root so relative commands like
     `./upgrade.sh` resolve correctly.
   - `ARTHEXIS_BASE_DIR` and `ARTHEXIS_LOG_DIR` for the watcher.
   - `StandardOutput`/`StandardError` appended to
     `logs/delegated-upgrade.log` for easy inspection.
3. The transient unit runs `/usr/local/bin/watch-upgrade`, which:
   - Stops the managed service.
   - Executes `upgrade.sh` (default `--stable`; Celery can pass `--latest`).
   - Restarts the service and exits with the upgrade status.
4. Celery schedules a post-upgrade health check after the run to confirm HTTP
   200 responses and records any failures.

## Triggering an upgrade manually

Run one of the following from the project root:

```bash
./upgrade.sh --stable   # direct run, useful for local validation
./upgrade.sh --detached # launches the delegated watcher so the upgrade continues if the console disconnects
./scripts/delegated-upgrade.sh  # matches the automated delegated path
```

You can also request the Celery task:

```bash
# Django shell or worker context
from apps.core.tasks import check_github_updates
check_github_updates.delay()
```

## Observing progress

- Transient unit: `journalctl -u delegated-upgrade-<timestamp>.service`
- Delegated logs: `logs/delegated-upgrade.log`
- Watcher logs: `logs/watch-upgrade.log`
- Auto-upgrade timeline: `logs/auto-upgrade.log`

## Common issues

- **watch-upgrade missing**: rerun `./env-refresh.sh` to install the helper.
- **Permission denied copying watch-upgrade**: `/usr/local/bin` is usually
  root-owned, so the copy step in `env-refresh.sh` needs write access. Grant the
  invoking user permission to write to `/usr/local/bin` or ensure passwordless
  `sudo` is available so the helper can be installed. When the helper is
  missing, delegated upgrades cannot start because the transient systemd unit
  launches `/usr/local/bin/watch-upgrade`.
- **Permission denied on systemd-run**: grant the service user access or provide
  passwordless sudo for `systemd-run`.
- **Service not restarted**: ensure `.locks/service.lck` contains the correct
  unit name and that the unit exists in systemd.
