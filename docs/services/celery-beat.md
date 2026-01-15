# Celery beat

## What it is
Celery beat is the scheduler service that triggers periodic background tasks for Arthexis.

## What it does
- Publishes scheduled jobs (upgrade checks, LCD summaries, health tasks) into the Celery queue.
- Keeps periodic tasks running even when no users are active.

## Enable
1. Confirm Celery is enabled for the node (`.locks/celery.lck` exists).
2. Enable the systemd unit:
   ```bash
   sudo systemctl enable --now celery-beat-<service-name>.service
   ```

## Disable
1. Stop and disable the unit:
   ```bash
   sudo systemctl disable --now celery-beat-<service-name>.service
   ```
2. Remove the lock to disable all Celery services:
   ```bash
   rm -f .locks/celery.lck
   ```
3. Set `ARTHEXIS_DISABLE_CELERY=true` to force-disable Celery startup in environments where the lock must remain.

## Notes
- Celery beat is typically paired with the Celery worker; disabling the lock disables both.
