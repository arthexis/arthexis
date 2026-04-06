# Suite startup sequence

The startup flow is intentionally layered so startup intelligence lives in the
suite, while shell remains a thin transport/process launcher.

- **Shell transport (`scripts/service-start.sh`)** handles environment activation,
  logging plumbing, static asset sync, and process spawning.
- **Suite intelligence (`manage.py startup_orchestrate`)** handles startup
  decisions, preflight checks, and startup metadata artifacts.

Environment refreshes are not triggered automatically during start; they are
reserved for manual runs of `env-refresh.sh` or calls made as part of an
upgrade.

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
   current filesystem. Collectstatic behavior is controlled by
   `ARTHEXIS_COLLECTSTATIC_POLICY=apply|check|skip` (`apply` is the default):
   `apply` runs `manage.py collectstatic --noinput` when assets are stale,
   `check` fails startup if assets are stale, and `skip` bypasses the static
   assets preflight entirely. `--force-collectstatic` always forces `apply`.
4. Invoke `manage.py startup_orchestrate` and consume its JSON contract to keep
   shell branching deterministic (`launch.celery_embedded`,
   `launch.lcd_embedded`, `launch.lcd_target_mode`, and structured check
   statuses).
5. Launch embedded Celery worker/beat only when orchestration says embedded
   mode is required.
6. Launch embedded LCD only when orchestration says embedded mode is required;
   otherwise start the systemd LCD unit when the orchestrator resolves systemd
   ownership.
7. Launch the Django server on `0.0.0.0:<port>`, using `--noreload` unless
   `--reload` was requested.

## Startup orchestration (`manage.py startup_orchestrate`)
1. Evaluate lock/feature state for service mode, Celery units, and LCD feature
   enablement.
2. Record startup metadata in `.locks/startup_started_at.lck`.
3. Run preflight checks (`run_runserver_preflight`) and startup maintenance
   (`manage.py startup_maintenance`), emitting structured statuses for each.
4. Queue LCD startup messaging when LCD is enabled.
5. Write orchestration status metadata to
   `.locks/startup_orchestrate_status.lck` and emit a JSON launch contract.

### Migration policy tuning by environment

`run_runserver_preflight` supports `ARTHEXIS_MIGRATION_POLICY=apply|check|skip`.

- `check`: verify migration state and fail fast when migrations are pending.
- `apply`: apply pending migrations during startup.
- `skip`: bypass migration preflight entirely (recommended only when your
  deployment pipeline guarantees migrations have already been applied).

Default behavior remains role-based (`satellite`/`watchtower` default to
`check`; other roles default to `apply`). For upgrades of existing
installations, set `ARTHEXIS_MIGRATION_POLICY=check` on routine app nodes to
reduce blocking startup time. Reserve `apply` for nodes/environments that are
explicitly responsible for schema rollout.

### Collectstatic policy tuning by environment

`scripts/service-start.sh` supports
`ARTHEXIS_COLLECTSTATIC_POLICY=apply|check|skip`.

- `apply`: run `collectstatic` when static assets are stale.
- `check`: verify static assets are current and fail fast when they are stale.
- `skip`: bypass static assets preflight entirely (recommended only when your
  deployment pipeline guarantees static assets are already prepared).

Fresh installs default to `apply`. For upgrades of existing installations, set
`ARTHEXIS_COLLECTSTATIC_POLICY=check` on routine app nodes to reduce blocking
startup work. Reserve `apply` for nodes/environments that are explicitly
responsible for static asset rollout.

## Operational cleanup ownership

Import-time hooks in `AppConfig.ready()` are limited to signal/module wiring only.
Operational cleanup is owned by app-level maintenance entry points:

- OCPP cached status reset: `manage.py reset_cached_statuses` or
  `apps.ocpp.tasks.reset_cached_statuses`.
- Sites view history purge: `manage.py purge_view_history` or
  `apps.sites.tasks.purge_view_history` (scheduled by Celery Beat).
- Cross-app startup orchestration: `manage.py startup_maintenance` (invoked by
  startup scripts before `runserver` and available for manual runs).

This keeps startup side effects explicit and discoverable from operational
commands and scheduler configuration instead of import-time behavior.
