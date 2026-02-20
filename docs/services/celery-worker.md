# Celery worker

## What it is
The Celery worker is the background job processor for Arthexis. It executes asynchronous tasks such as email delivery, upgrade checks, and LCD log summaries.

## What it does
- Consumes tasks from the configured broker.
- Runs long-lived background work without blocking the main web service.

## Enable
1. Ensure the Celery lock exists:
   ```bash
   touch .locks/celery.lck
   ```
   The `install.sh` presets typically create this lock automatically.
2. If running via systemd, install and enable the unit:
   ```bash
   sudo systemctl enable --now celery-<service-name>.service
   ```

## Disable
1. Stop and disable the systemd unit (if installed):
   ```bash
   sudo systemctl disable --now celery-<service-name>.service
   ```
2. Remove the lock file to disable Celery entirely:
   ```bash
   rm -f .locks/celery.lck
   ```
3. You can also set `ARTHEXIS_DISABLE_CELERY=true` in the environment to force-disable Celery startup.

## Notes
- The Suite Services Report still lists the Celery worker row when disabled so operators know the unit name to enable later.

- The worker starts with a unique worker node name (`-n worker.<service>@%h`) to avoid `DuplicateNodenameWarning` when multiple nodes share the same host.
