# Install & Lifecycle Scripts Manual

This manual explains how to deploy, operate, and retire an Arthexis node with the platform's shell and batch helpers. It is organised in the order you typically run them: installation, runtime start/stop helpers, upgrades, and uninstallation. Each section includes all supported flags and highlights how the scripts interact with services, lock files, and other subsystems.

## Git remotes for preloaded environments

> If this repository is provided without Git remotes configured, `configure.sh`, `install.sh`, and `upgrade.sh` automatically add the official Arthexis GitHub remote as `upstream`. If no `origin` is configured, they also set `origin` to `https://github.com/arthexis/arthexis`. You can override `origin` later if you prefer a fork-based workflow.

## 1. Installation helpers

### 1.1 Linux: `install.sh`

`install.sh` prepares a virtual environment, configures Nginx, seeds lock files, and optionally provisions systemd units. Run it from the repository root on a Debian/Ubuntu compatible host. The script stores its log in `logs/install.log` and exits on any error to prevent partial installations.【F:install.sh†L1-L39】【F:install.sh†L480-L563】

Single-instance deployments are now the standard: `install.sh` no longer manages
sibling primary/secondary installs, and each node should run exactly one suite
instance.

**Role presets**

Passing a role flag applies a curated bundle of options. Each preset still honours additional command-line overrides you append afterwards.【F:install.sh†L218-L340】

| Flag | Description |
| --- | --- |
| `--terminal` (default) | Local workstation profile. Uses the internal Nginx template, reserves port 8888 unless overridden, enables Celery for email delivery, and defaults to fixed upgrades unless you pass `--unstable`/`--latest` or `--stable`.【F:install.sh†L312-L317】 |
| `--control` | Appliance profile. Requires Nginx and Redis, enables Celery, LCD, Control-specific locks, internal Nginx, and writes the `Control` role lock. Starts services automatically unless `--no-start` overrides and leaves upgrades fixed unless you select a channel with `--unstable`/`--latest` or `--stable`. Sets default service name `arthexis`.【F:install.sh†L24-L47】【F:install.sh†L319-L333】【F:install.sh†L320-L341】 |
| `--satellite` | Edge node profile. Requires Nginx and Redis, enables Celery, uses the internal Nginx template, and leaves upgrades fixed unless you add `--stable` or `--unstable`.【F:install.sh†L303-L310】 |
| `--watchtower` | Multi-tenant profile. Requires Nginx and Redis, keeps Celery on, switches to the public Nginx proxy with HTTPS expectations, and defaults to fixed upgrades until you choose `--stable` or `--unstable`.【F:install.sh†L334-L340】 |

**General options**

| Flag | Purpose |
| --- | --- |
| `--service NAME` | Installs or updates systemd services (`NAME`, optionally with `lcd-NAME`, `rfid-NAME`, and `camera-NAME` companions) and records the name in `.locks/service.lck` for runtime helpers.【F:install.sh†L663】【F:install.sh†L672-L674】 |
| `--port PORT` | Overrides the backend Django port used in generated systemd units and the stored lock. If omitted, every mode defaults to `8888`.【F:install.sh†L234-L237】 |
| `--upgrade` | Immediately runs `upgrade.sh` after installation, using the selected channel (stable by default, unstable when requested).【F:install.sh†L239-L242】【F:install.sh†L578-L599】 |
| `--auto-upgrade` | Explicitly enables unattended upgrades (off by default) and refreshes the Celery schedule when locks exist.【F:install.sh†L243-L259】【F:install.sh†L578-L603】 |
| `--fixed` | Disables unattended upgrades and removes the auto-upgrade lock so future runs stay manual-only.【F:install.sh†L247-L259】【F:install.sh†L601-L603】 |
| `--unstable` / `--latest` | Enables auto-upgrade on the unstable channel that follows origin/main revisions immediately.【F:install.sh†L251-L259】【F:install.sh†L578-L599】 |
| `--stable` / `--regular` / `--normal` | Enables auto-upgrade on the stable release channel with weekly Thursday-morning checks (before 5:00 AM).【F:install.sh†L256-L259】【F:install.sh†L578-L599】 |
| `--celery` | Enables Celery support (`ENABLE_CELERY=true`) without changing service-management mode; pair with `--embedded` or `--systemd` when you need to force runtime mode explicitly.【F:install.sh†L238-L250】【F:install.sh†L361-L366】 |
| `--lcd-screen` / `--no-lcd-screen` | Adds or removes the LCD updater service and lock. Control preset enables it automatically; `--no-lcd-screen` removes an existing unit after reading `.locks/service.lck`.【F:install.sh†L275-L333】【F:install.sh†L526-L575】 |
| `--clean` | Deletes `db.sqlite3` before installing, after first backing it up into `backups/` with version and Git metadata.【F:install.sh†L61-L120】 |
| `--start` / `--no-start` | Launches or skips `start.sh` after setup completes, which is useful for unattended provisioning while still allowing explicit opt-outs.【F:install.sh†L24-L47】【F:install.sh†L289-L297】【F:install.sh†L611-L613】 |

The script also:
- Verifies Nginx and Redis availability for roles that require them, writing `redis.env` when Redis is configured.【F:install.sh†L61-L200】【F:install.sh†L303-L340】
- Creates `.venv`, installs dependencies via `scripts/helpers/pip_install.py`, applies migrations, and refreshes environment secrets via `env-refresh.sh`.【F:install.sh†L430-L515】
- Writes lock files capturing the selected role, Nginx mode, and enabled subsystems so the runtime helpers know how to behave.【F:install.sh†L303-L373】
- Refreshes desktop shortcuts on Linux desktops for quick access to the UI and maintenance commands.【F:install.sh†L615-L615】

### 1.2 Windows: `install.bat`

The Windows installer is intentionally simple: it bootstraps `.venv`, installs requirements, applies migrations, and then runs `env-refresh.bat --latest` so developers always start on the latest schema locally.【F:install.bat†L1-L20】 Windows nodes rely on Terminal defaults (development server at port 8888), but you can optionally install a Windows service using `service.bat` once `install.bat` completes. Run `install.bat` again whenever dependencies change.

## 2. Runtime helpers

### 2.1 Linux: `start.sh`

`start.sh` activates the virtual environment, loads `*.env` files, optionally restarts provisioned systemd units, and then launches Django. Static assets are hashed before running `collectstatic`, saving time on repeated starts.【F:start.sh†L1-L66】【F:start.sh†L94-L143】 Supported options:

| Flag | Purpose |
| --- | --- |
| `--reload` | Passes through to `scripts/service-start.sh` to run Django with auto-reload enabled (useful for development).【F:start.sh†L39-L48】【F:scripts/service-start.sh†L228-L236】 |
| `--debug` | Enables Django debug mode and forwards the flag to the service launcher. You can also trigger debug mode by combining `--show DEBUG` with optional log following.【F:start.sh†L49-L61】【F:scripts/service-start.sh†L237-L239】【F:scripts/service-start.sh†L300-L309】 |
| `--show LEVEL` | Normalizes log levels (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`) and can be paired with `--log-follow` to stream filtered logs while startup proceeds.【F:start.sh†L50-L61】【F:scripts/service-start.sh†L240-L248】【F:scripts/service-start.sh†L290-L299】 |
| `--log-follow` | Streams application logs during startup (defaults to `INFO` when no `--show` value is supplied).【F:start.sh†L62-L64】【F:scripts/service-start.sh†L284-L289】 |
| `--clear-logs` | Stops relevant services, deletes existing log files, then starts with fresh logs. This is useful when debugging startup issues from a clean log baseline.【F:start.sh†L65-L95】 |

When a systemd service was previously installed, the script prefers restarting those units (plus optional `rfid-*` and `camera-*` companions) and exits once they are healthy. Otherwise it delegates to `scripts/service-start.sh` for embedded startup behavior in the foreground.【F:start.sh†L168-L256】

### 2.2 Linux: `stop.sh`

`stop.sh` mirrors the start helper: it stops systemd units when present, including Celery and LCD services, or falls back to killing `manage.py runserver` and Celery processes. It respects non-interactive sudo where available and displays service status for confirmation.【F:stop.sh†L1-L69】 Use:

- `stop.sh PORT` (default `8888`) to stop only the server bound to that port.【F:stop.sh†L70-L103】
- `stop.sh --all` to stop every matching runserver process regardless of port.【F:stop.sh†L70-L109】

On Control nodes with LCD support, it also sends a farewell notification before shutting down the display updater.【F:stop.sh†L32-L68】【F:stop.sh†L110-L129】


### 2.3 Documentation maintenance check

When updating lifecycle script docs, verify every documented flag against each script parser before merging:

1. Check the script `usage()` string for the top-level supported interface.
2. Check the `case "$1" in ...` parser block to confirm accepted flags, aliases, and removed options.
3. Keep examples limited to commands that the current parser accepts (`install.sh` and `start.sh` first, then any delegated helpers they call).

This lightweight check prevents stale docs when options are added, renamed, or removed.【F:install.sh†L63】【F:install.sh†L204-L337】【F:start.sh†L36-L74】【F:scripts/service-start.sh†L234-L297】

### 2.4 Windows: `start.bat`

The Windows starter mirrors the Linux workflow without service management: it validates `.venv`, reruns `collectstatic` only when the static hash changes, and starts Django at the requested port (default 8888). Flags: `--port PORT` and `--reload`. Any other argument prints usage and aborts.【F:start.bat†L1-L57】 Use `Ctrl+C` to stop the server.

For background operation, install the suite as a Windows service with `service.bat install`. The helper uses NSSM (the Non-Sucking Service Manager); install `nssm.exe` and make sure it is on your PATH (or pass `--nssm` with the full path). Example:

```
service.bat install --name arthexis --port 8888
```

## 3. Upgrades

### 3.1 Linux: `upgrade.sh`

`upgrade.sh` keeps nodes current while protecting local changes. It infers the node role from `.locks/role.lck` to decide whether local commits should be discarded (Control/Constellation/Watchtower nodes auto-align to the remote branch).【F:upgrade.sh†L123-L205】

Supported options:

| Flag | Purpose |
| --- | --- |
| `--latest` / `-l` / `--unstable` | Follows the unstable channel, upgrading whenever the origin/main revision changes even if `VERSION` remains the same.【F:upgrade.sh†L249-L285】【F:upgrade.sh†L520-L550】 |
| `--stable` / `--regular` / `--normal` | Uses the stable channel, aligning with release revisions and the weekly Thursday-morning auto-upgrade cadence (before 5:00 AM).【F:upgrade.sh†L249-L285】【F:upgrade.sh†L520-L550】 |
| `--clean` | Deletes `db.sqlite3` (and any `db_*.sqlite3` snapshots) after confirmation so migrations start from a blank database.【F:upgrade.sh†L122-L167】【F:upgrade.sh†L420-L444】 |
| `--clear-logs` | Removes existing log files so the next start writes fresh logs without previous entries.【F:upgrade.sh†L1024-L1061】【F:upgrade.sh†L1505-L1512】 |
| `--clear-work` | Deletes the contents of `work/` before restarting, keeping temporary run artifacts from carrying over.【F:upgrade.sh†L1063-L1069】【F:upgrade.sh†L1505-L1512】 |
| `--start` / `--no-start` | Forces services to start after the upgrade (even if they were previously stopped) or keeps them offline afterwards; `--no-start` also accepts the legacy `--no-restart` alias.【F:upgrade.sh†L123-L144】【F:upgrade.sh†L404-L419】【F:upgrade.sh†L514-L551】 |
| `--force` / `-f` | Forces services to stop and upgrades to proceed even when they would normally be blocked (for example, dirty trees or running services).【F:upgrade.sh†L680-L748】【F:upgrade.sh†L1168-L1211】 |
| `--no-warn` | Skips interactive confirmation before destructive database operations (used with `--clean` or uninstall flows).【F:upgrade.sh†L122-L167】 |

Additional behaviour:
- Before applying migrations it refreshes Nginx maintenance assets, optionally clears the database, reruns `env-refresh.sh`, migrates legacy systemd configurations, and restarts services unless `--no-start`/`--no-restart` was requested.【F:upgrade.sh†L404-L551】
- After restarting, it updates desktop shortcuts so GUI launchers stay current.【F:upgrade.sh†L552-L555】

### 3.2 Windows: `upgrade.bat`

The Windows upgrade helper focuses on Git safety and dependency refreshes. It pulls with rebase and reinstalls dependencies when the `requirements.txt` hash changes, using `scripts/helpers/pip_install.py` when available.【F:upgrade.bat†L1-L28】

## 4. Uninstallation

### 4.1 Linux: `uninstall.sh`

`uninstall.sh` removes system services, lock files, and the SQLite database. It warns before deleting persistent data unless you pass `--no-warn` (useful for scripted teardown). Providing `--service NAME` overrides the autodetected service from `.locks/service.lck`.【F:uninstall.sh†L1-L77】【F:uninstall.sh†L90-L159】 The script always prompts before stopping the server, disables any LCD and Celery units, and refreshes desktop shortcuts on exit.【F:uninstall.sh†L90-L167】

### 4.2 Windows

Use `service.bat remove --name <service-name>` to uninstall the Windows service when you no longer need it. After removing the service, you can delete the project directory manually and remove the virtual environment folder if disk space matters.

---

**Quick reference**

1. Install (`install.sh` or `install.bat`).
2. Start (`start.sh` / `start.bat`) and stop (`stop.sh`).
3. Upgrade periodically (`upgrade.sh` / `upgrade.bat`).
4. Tear down with `uninstall.sh` on Linux.
