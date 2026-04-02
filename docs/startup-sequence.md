# Suite startup sequence

The following steps describe what happens during a normal suite startup,
covering both the manual entry point and the main service launcher. Environment
refreshes are not triggered automatically during start; they are reserved for
manual runs of `env-refresh.sh` or calls made as part of an upgrade.

## Manual entry point (`start.sh`)
- `start.sh` writes a note to `logs/start.log` to record the manual request.
- If a managed service name exists in `.locks/service.lck` and systemd is
  available, it restarts that unit, watches for it to reach `active`, and exits
  early if successful. Otherwise, it falls back to running `scripts/service-start.sh`
  with any additional arguments.

## Main service launcher (`scripts/service-start.sh`)
1. Resolve the log directory, tee output into `logs/service-start.log`, mirror
   stderr into `logs/error.log`, and load helper functions. At the start of
   each boot, existing `*.log` files receive a clear startup-break marker so
   each attempt is easy to distinguish without deleting prior history.
2. Ensure the virtual environment exists, activate it, and load any `*.env`
   files into the environment for downstream commands.
3. Reuse `.locks/staticfiles.md5` together with `.locks/staticfiles.meta` to
   avoid re-hashing static assets when the recorded mtime snapshot matches the
   current filesystem. When the metadata is stale (or
   `--force-collectstatic` is provided), compute a new hash with
   `scripts/staticfiles_md5.py`, refresh both lock files, and run
   `manage.py collectstatic --noinput` if the hash differs.
4. During runserver preflight, generate a fast metadata snapshot for
   `apps/**/migrations/*.py` (relative path + mtime + size) and compare it to
   `.locks/migrations.meta`. If it matches exactly and `.locks/migrations.sha`
   is present, reuse the stored fingerprint and skip recomputing the full
   content hash. When metadata differs, metadata/fingerprint cache files are
   missing or invalid, or `RUNSERVER_PREFLIGHT_FORCE_REFRESH=true` is set, the
   launcher recomputes the full fingerprint and refreshes both lock files.
5. Even when fingerprint reuse is possible, preflight still runs
   `manage.py migrate --check` before declaring startup migration checks
   complete.
6. Detect the backend port, parse CLI flags (reload mode, port overrides, and
   Celery management preferences), and evaluate whether systemd-managed Celery
   or LCD units are present so embedded workers are enabled only when needed.
   Record the startup timestamp and chosen port in
   `.locks/startup_started_at.lck` for status reporting.
7. When the LCD feature flag is enabled, queue a startup Net Message via
   `apps.screens.startup_notifications.queue_startup_message` to record the hostname
   and port for the boot cycle.
8. Start embedded Celery worker and beat processes unless Celery management is
   disabled or delegated to systemd, capturing their PIDs for cleanup.
9. If the LCD is configured for embedded mode, start the `apps.screens.lcd_screen`
   process alongside the web server.
10. Launch the Django server on `127.0.0.1:<port>` by default, using `--noreload`
   unless `--reload` was requested. Service scripts that need LAN exposure pass an
   explicit bind address instead of relying on the CLI default.


## Operational cleanup ownership

Import-time hooks in `AppConfig.ready()` are limited to signal/module wiring only.
Operational cleanup is owned by app-level maintenance entry points:

- OCPP cached status reset: `manage.py reset_cached_statuses` or
  `apps.ocpp.tasks.reset_cached_statuses`.
- Sites view history purge: `manage.py purge_view_history` or
  `apps.sites.tasks.purge_view_history` (scheduled by Celery Beat).
- Cross-app startup orchestration: `manage.py startup_maintenance`.

This keeps startup side effects explicit and discoverable from operational
commands and scheduler configuration instead of import-time behavior.
