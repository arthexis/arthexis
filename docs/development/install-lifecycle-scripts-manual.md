# Install & Lifecycle Scripts Manual

This manual explains how to deploy, operate, and retire an Arthexis node with the platform's shell and batch helpers. It is organised in the order you typically run them: installation, runtime start/stop helpers, upgrades, and uninstallation. Each section includes all supported flags and highlights how the scripts interact with services, lock files, and other subsystems.

## Git remotes for preloaded environments

> If this repository is provided without Git remotes configured, `configure.sh`, `install.sh`, and `upgrade.sh` automatically add the official Arthexis GitHub remote as `upstream`. If no `origin` is configured, they also set `origin` to `https://github.com/arthexis/arthexis`. You can override `origin` later if you prefer a fork-based workflow.

## 1. Installation helpers

### 1.1 Linux: `install.sh`

`install.sh` prepares a virtual environment, configures Nginx, seeds lock files, and optionally provisions systemd units. Run it from the repository root on a Debian/Ubuntu-compatible host. The script stores its log in `logs/install.log` and exits on any error to prevent partial installations.

Single-instance deployments are now the standard: `install.sh` no longer manages
sibling primary/secondary installs, and each node should run exactly one suite
instance.

**Role presets**

Passing a role flag applies a curated bundle of options. Each preset still honours additional command-line overrides you append afterwards.

| Flag | Description |
| --- | --- |
| `--terminal` (default) | Local workstation profile. Uses the internal Nginx template, reserves port 8888 unless overridden, enables Celery for email delivery, and defaults to fixed upgrades unless you pass `--unstable`/`--latest` or `--stable`. |
| `--control` | Appliance profile. Requires Nginx and Redis, enables Celery, LCD, Control-specific locks, internal Nginx, and writes the `Control` role lock. Starts services automatically unless `--no-start` overrides and leaves upgrades fixed unless you select a channel with `--unstable`/`--latest` or `--stable`. Sets default service name `arthexis`. |
| `--satellite` | Edge node profile. Requires Nginx and Redis, enables Celery, uses the internal Nginx template, and leaves upgrades fixed unless you add `--stable` or `--unstable`. |
| `--watchtower` | Multi-tenant profile. Requires Nginx and Redis, keeps Celery on, switches to the public Nginx proxy with HTTPS expectations, and defaults to fixed upgrades until you choose `--stable` or `--unstable`. |

**General options**

| Flag | Purpose |
| --- | --- |
| `--service NAME` | Installs or updates systemd services (`NAME`, optionally with `lcd-NAME`, `rfid-NAME`, and `camera-NAME` companions) and records the name in `.locks/service.lck` for runtime helpers. |
| `--port PORT` | Overrides the backend Django port used in generated systemd units and the stored lock. If omitted, every mode defaults to `8888`. |
| `--upgrade` | Immediately runs `upgrade.sh` after installation, using the selected channel (stable by default, unstable when requested). |
| `--auto-upgrade` | Explicitly enables unattended upgrades (off by default) and refreshes the Celery schedule when locks exist. |
| `--fixed` | Disables unattended upgrades and removes the auto-upgrade lock so future runs stay manual-only. |
| `--unstable` / `--latest` | Enables auto-upgrade on the unstable channel that follows origin/main revisions immediately. |
| `--stable` / `--regular` / `--normal` | Enables auto-upgrade on the stable release channel with weekly Thursday-morning checks (before 5:00 AM). |
| `--celery` | Enables Celery support (`ENABLE_CELERY=true`) without changing service-management mode; pair with `--embedded` or `--systemd` when you need to force runtime mode explicitly. |
| `--lcd-screen` / `--no-lcd-screen` | Adds or removes the LCD updater service and lock. Control preset enables it automatically; `--no-lcd-screen` removes an existing unit after reading `.locks/service.lck`. |
| `--clean` | Deletes `db.sqlite3` before installing, after first backing it up into `backups/` with version and Git metadata. |
| `--start` / `--no-start` | Launches or skips `start.sh` after setup completes, which is useful for unattended provisioning while still allowing explicit opt-outs. |

The script also:
- Verifies Nginx and Redis availability for roles that require them, writing `redis.env` when Redis is configured.
- Creates `.venv`, installs dependencies via `scripts/helpers/pip_install.py`, applies migrations, and refreshes environment secrets via `env-refresh.sh`.
- Writes lock files capturing the selected role, Nginx mode, and enabled subsystems so the runtime helpers know how to behave.
- Refreshes desktop shortcuts on Linux desktops for quick access to the UI and maintenance commands.

### 1.2 Windows: `install.bat`

The Windows installer is intentionally simple: it bootstraps `.venv`, installs requirements, applies migrations, and then runs `env-refresh.bat --latest` so developers always start on the latest schema locally. Windows nodes rely on Terminal defaults (development server at port 8888), but you can optionally install a Windows service using `service.bat` once `install.bat` completes. Run `install.bat` again whenever dependencies change.

## 2. Runtime helpers

### 2.1 Linux: `start.sh`

`start.sh` activates the virtual environment, loads `*.env` files, optionally restarts provisioned systemd units, and then launches Django. Static assets are hashed before running `collectstatic`, saving time on repeated starts. Supported options:

| Flag | Purpose |
| --- | --- |
| `--reload` | Passes through to `scripts/service-start.sh` to run Django with auto-reload enabled (useful for development). |
| `--debug` | Enables Django debug mode and forwards the flag to the service launcher. You can also trigger debug mode by combining `--show DEBUG` with optional log following. |
| `--show LEVEL` | Normalizes log levels (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`) and can be paired with `--log-follow` to stream filtered logs while startup proceeds. |
| `--log-follow` | Streams application logs during startup (defaults to `INFO` when no `--show` value is supplied). |
| `--clear-logs` | Stops relevant services, deletes existing log files, then starts with fresh logs. This is useful when debugging startup issues from a clean log baseline. |

When a systemd service was previously installed, the script prefers restarting those units (plus optional `rfid-*` and `camera-*` companions) and exits once they are healthy. Otherwise it delegates to `scripts/service-start.sh` for embedded startup behavior in the foreground.

### 2.2 Linux: `stop.sh`

`stop.sh` mirrors the start helper: it stops systemd units when present, including Celery and LCD services, or falls back to killing `manage.py runserver` and Celery processes. It respects non-interactive sudo where available and displays service status for confirmation. Use:

- `stop.sh PORT` (default `8888`) to stop only the server bound to that port.
- `stop.sh --all` to stop every matching runserver process regardless of port.

On Control nodes with LCD support, it also sends a farewell notification before shutting down the display updater.


### 2.3 Documentation maintenance check

When updating lifecycle script docs, verify every documented flag against each script parser before merging:

1. Check the script `usage()` string for the top-level supported interface.
2. Check the `case "$1" in...` parser block to confirm accepted flags, aliases, and removed options.
3. Keep examples limited to commands that the current parser accepts (`install.sh` and `start.sh` first, then any delegated helpers they call).

This lightweight check prevents stale docs when options are added, renamed, or removed.

### 2.4 Windows: `start.bat`

The Windows starter mirrors the Linux workflow without service management: it validates `.venv`, reruns `collectstatic` only when the static hash changes, and starts Django at the requested port (default 8888). Flags: `--port PORT` and `--reload`. Any other argument prints usage and aborts. Use `Ctrl+C` to stop the server.

For background operation, install the suite as a Windows service with `service.bat install`. The helper uses NSSM (the Non-Sucking Service Manager); install `nssm.exe` and make sure it is on your PATH (or pass `--nssm` with the full path). Example:

```
service.bat install --name arthexis --port 8888
```

## 3. Upgrades

### 3.1 Linux: `upgrade.sh`

`upgrade.sh` keeps nodes current while protecting local changes. It infers the node role from `.locks/role.lck` to decide whether local commits should be discarded (Control/Constellation/Watchtower nodes auto-align to the remote branch).

Supported options:

| Flag | Purpose |
| --- | --- |
| `--latest` / `-l` / `--unstable` | Follows the unstable channel, upgrading whenever the origin/main revision changes even if `VERSION` remains the same. |
| `--stable` / `--regular` / `--normal` | Uses the stable channel, aligning with release revisions and the weekly Thursday-morning auto-upgrade cadence (before 5:00 AM). |
| `--clean` | Deletes `db.sqlite3` (and any `db_*.sqlite3` snapshots) after confirmation so migrations start from a blank database. |
| `--clear-logs` | Removes existing log files so the next start writes fresh logs without previous entries. |
| `--clear-work` | Deletes the contents of `work/` before restarting, keeping temporary run artifacts from carrying over. |
| `--start` / `--no-start` | Forces services to start after the upgrade (even if they were previously stopped) or keeps them offline afterwards; `--no-start` also accepts the legacy `--no-restart` alias. |
| `--force` / `-f` | Forces services to stop and upgrades to proceed even when they would normally be blocked (for example, dirty trees or running services). |
| `--no-warn` | Skips interactive confirmation before destructive database operations (used with `--clean` or uninstall flows). |

Additional behaviour:
- Before applying migrations it refreshes Nginx maintenance assets, optionally clears the database, reruns `env-refresh.sh`, migrates legacy systemd configurations, and restarts services unless `--no-start`/`--no-restart` was requested.
- After restarting, it updates desktop shortcuts so GUI launchers stay current.

### 3.2 Windows: `upgrade.bat`

The Windows upgrade helper focuses on Git safety and dependency refreshes. It pulls with rebase and reinstalls dependencies when the `requirements.txt` hash changes, using `scripts/helpers/pip_install.py` when available.

## 4. Uninstallation

### 4.1 Linux: `uninstall.sh`

`uninstall.sh` removes system services, lock files, and the SQLite database. It warns before deleting persistent data unless you pass `--no-warn` (useful for scripted teardown). Providing `--service NAME` overrides the autodetected service from `.locks/service.lck`. The script always prompts before stopping the server, disables any LCD and Celery units, and refreshes desktop shortcuts on exit.

### 4.2 Windows

Use `service.bat remove --name <service-name>` to uninstall the Windows service when you no longer need it. After removing the service, you can delete the project directory manually and remove the virtual environment folder if disk space matters.

---

**Quick reference**

1. Install (`install.sh` or `install.bat`).
2. Start (`start.sh` / `start.bat`) and stop (`stop.sh`).
3. Upgrade periodically (`upgrade.sh` / `upgrade.bat`).
4. Tear down with `uninstall.sh` on Linux.
