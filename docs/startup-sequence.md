# Suite startup sequence

The following steps describe what happens during a normal suite startup,
covering both the manual entry point and the main service launcher. Environment
refreshes are not triggered automatically during start; they are reserved for
manual runs of `env-refresh.sh` or calls made as part of an upgrade.

## Manual entry point (`start.sh`)
- `start.sh` writes a note to `logs/start.log` and creates a one-time
  `locks/service-start-skip.lck` file so the upcoming boot skips the auto-upgrade
  check once.
- If a managed service name exists in `locks/service.lck` and systemd is
  available, it restarts that unit, watches for it to reach `active`, and exits
  early if successful. Otherwise, it falls back to running `service-start.sh`
  with any additional arguments.

## Main service launcher (`service-start.sh`)
1. Resolve the log directory, tee output into `logs/service-start.log`, mirror
   stderr into `logs/error.log`, and load helper functions. The error log is
   truncated at the start of every boot so it only contains messages from the
   current attempt.
2. Ensure the virtual environment exists, activate it, and load any `*.env`
   files into the environment for downstream commands.
3. Check for a recent `service-start-skip.lck` lock to decide whether to skip a
   one-time auto-upgrade during this start; stale locks older than five minutes
   are ignored and removed.
4. If `locks/auto_upgrade.lck` is present and not skipped, run `upgrade.sh`
   (`--latest`/`--stable` when specified) before continuing with the rest of
   the startup, always passing `--no-restart` so service-start remains
   responsible for bringing the suite up after the upgrade.
5. Compute the static assets hash with `scripts/staticfiles_md5.py`; when the
   hash changes or cannot be computed, run `manage.py collectstatic --noinput`
   and cache the new hash in `staticfiles.md5`.
6. Detect the backend port, parse CLI flags (reload mode, port overrides, and
   Celery management preferences), and evaluate whether systemd-managed Celery
   or LCD units are present so embedded workers are enabled only when needed.
   Record the startup timestamp and chosen port in
   `locks/startup_started_at.lck` for status reporting.
7. When the LCD feature flag is enabled, queue a startup Net Message via
   `nodes.startup_notifications.queue_startup_message` to record the hostname
   and port for the boot cycle.
8. Start embedded Celery worker and beat processes unless Celery management is
   disabled or delegated to systemd, capturing their PIDs for cleanup.
9. If the LCD is configured for embedded mode, start the `core.lcd_screen`
   process alongside the web server.
10. Launch the Django server on `0.0.0.0:<port>`, using `--noreload` by default
    unless `--reload` was requested.
