# Install & Lifecycle Scripts Manual

This manual explains how to deploy, operate, and retire an Arthexis node with the platform's shell and batch helpers. It is organised in the order you typically run them: installation, runtime start/stop helpers, upgrades, and uninstallation. Each section includes all supported flags and highlights how the scripts interact with services, lock files, and other subsystems.

## 1. Installation helpers

### 1.1 Linux: `install.sh`

`install.sh` prepares a virtual environment, configures Nginx, seeds lock files, and optionally provisions systemd units. Run it from the repository root on a Debian/Ubuntu compatible host. The script stores its log in `logs/install.log` and exits on any error to prevent partial installations.【F:install.sh†L1-L39】【F:install.sh†L480-L563】

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
| `--service NAME` | Installs or updates systemd services (`NAME`, `celery-NAME`, `celery-beat-NAME`, and optionally `lcd-NAME`) and records the name in `.locks/service.lck` for the runtime helpers.【F:install.sh†L221-L225】【F:install.sh†L519-L524】 |
| `--internal` | Forces the internal Nginx template (HTTP ports 80/8000/8080/8900). This is the default unless a role preset changes it.【F:install.sh†L226-L229】【F:install.sh†L320-L373】 |
| `--public` | Enables the public HTTPS reverse proxy template while continuing to proxy to the backend on port 8888 unless overridden.【F:install.sh†L230-L233】【F:install.sh†L305-L373】 |
| `--port PORT` | Overrides the backend Django port used in generated systemd units and the stored lock. If omitted, every mode defaults to `8888`.【F:install.sh†L234-L237】 |
| `--upgrade` | Immediately runs `upgrade.sh` after installation, using the selected channel (stable by default, unstable when requested).【F:install.sh†L239-L242】【F:install.sh†L578-L599】 |
| `--auto-upgrade` | Explicitly enables unattended upgrades (off by default) and refreshes the Celery schedule when locks exist.【F:install.sh†L243-L259】【F:install.sh†L578-L603】 |
| `--fixed` | Disables unattended upgrades and removes the auto-upgrade lock so future runs stay manual-only.【F:install.sh†L247-L259】【F:install.sh†L601-L603】 |
| `--unstable` / `--latest` | Enables auto-upgrade on the unstable channel that follows origin/main revisions immediately.【F:install.sh†L251-L259】【F:install.sh†L578-L599】 |
| `--stable` / `--regular` / `--normal` | Enables auto-upgrade on the stable release channel with 24-hour checks.【F:install.sh†L256-L259】【F:install.sh†L578-L599】 |
| `--celery` | Forces Celery services on even if the preset would leave them disabled. Rarely needed because all presets already enable Celery.【F:install.sh†L261-L263】【F:install.sh†L320-L341】 |
| `--lcd-screen` / `--no-lcd-screen` | Adds or removes the LCD updater service and lock. Control preset enables it automatically; `--no-lcd-screen` removes an existing unit after reading `.locks/service.lck`.【F:install.sh†L275-L333】【F:install.sh†L526-L575】 |
| `--clean` | Deletes `db.sqlite3` before installing, after first backing it up into `backups/` with version and Git metadata.【F:install.sh†L61-L120】 |
| `--start` / `--no-start` | Launches or skips `start.sh` after setup completes, which is useful for unattended provisioning while still allowing explicit opt-outs.【F:install.sh†L24-L47】【F:install.sh†L289-L297】【F:install.sh†L611-L613】 |

The script also:

- Verifies Nginx and Redis availability for roles that require them, writing `redis.env` when Redis is configured.【F:install.sh†L61-L200】【F:install.sh†L303-L340】
- Creates `.venv`, installs dependencies via `scripts/helpers/pip_install.py`, applies migrations, and refreshes environment secrets via `env-refresh.sh`.【F:install.sh†L430-L515】
- Writes lock files capturing the selected role, Nginx mode, and enabled subsystems so the runtime helpers know how to behave.【F:install.sh†L303-L373】
- Refreshes desktop shortcuts on Linux desktops for quick access to the UI and maintenance commands.【F:install.sh†L615-L615】

### 1.2 Windows: `install.bat`

The Windows installer is intentionally simple: it bootstraps `.venv`, installs requirements, applies migrations, and then runs `env-refresh.bat --latest` so developers always start on the latest schema locally.【F:install.bat†L1-L20】 No flags are accepted—Windows nodes rely on Terminal defaults (development server at port 8888, no system services). Run `install.bat` again whenever dependencies change.

## 2. Runtime helpers

### 2.1 Linux: `start.sh`

`start.sh` activates the virtual environment, loads `*.env` files, optionally restarts provisioned systemd units, and then launches Django. Static assets are hashed before running `collectstatic`, saving time on repeated starts.【F:start.sh†L1-L66】【F:start.sh†L94-L143】 Supported options:

| Flag | Purpose |
| --- | --- |
| `--port PORT` | Overrides the serving port; defaults to `8888` regardless of the Nginx mode lock.【F:start.sh†L72-L112】 |
| `--reload` | Runs Django with auto-reload enabled (useful for development).【F:start.sh†L106-L117】【F:start.sh†L136-L145】 |
| `--celery` / `--no-celery` | Enables or disables the Celery worker/beat pair that handles background email. Defaults to on.【F:start.sh†L100-L145】 |
| `--public` / `--internal` | Convenience shorthands to reset the port to the installer default (8888) without editing the lock file.【F:start.sh†L108-L119】 |

When a systemd service was previously installed, the script prefers restarting those units (plus `celery-*` and `lcd-*` companions) and exits once they are healthy. Otherwise it runs the development server in the foreground.【F:start.sh†L46-L101】 Celery processes launched directly are cleaned up automatically when the script exits because of the `trap` handler.【F:start.sh†L120-L145】

### 2.2 Linux: `stop.sh`

`stop.sh` mirrors the start helper: it stops systemd units when present, including Celery and LCD services, or falls back to killing `manage.py runserver` and Celery processes. It respects non-interactive sudo where available and displays service status for confirmation.【F:stop.sh†L1-L69】 Use:

- `stop.sh PORT` (default `8888`) to stop only the server bound to that port.【F:stop.sh†L70-L103】
- `stop.sh --all` to stop every matching runserver process regardless of port.【F:stop.sh†L70-L109】

On Control nodes with LCD support, it also sends a farewell notification before shutting down the display updater.【F:stop.sh†L32-L68】【F:stop.sh†L110-L129】

### 2.3 Windows: `start.bat`

The Windows starter mirrors the Linux workflow without service management: it validates `.venv`, reruns `collectstatic` only when the static hash changes, and starts Django at the requested port (default 8888). Flags: `--port PORT` and `--reload`. Any other argument prints usage and aborts.【F:start.bat†L1-L57】 Use `Ctrl+C` to stop the server.

## 3. Upgrades

### 3.1 Linux: `upgrade.sh`

`upgrade.sh` keeps nodes current while protecting local changes. It infers the node role from `.locks/role.lck` to decide whether local commits should be discarded (Control/Constellation/Watchtower nodes auto-align to the remote branch).【F:upgrade.sh†L123-L205】

Supported options:

| Flag | Purpose |
| --- | --- |
| `--latest` / `--unstable` | Follows the unstable channel, upgrading whenever the origin/main revision changes even if `VERSION` remains the same.【F:upgrade.sh†L249-L285】【F:upgrade.sh†L520-L550】 |
| `--stable` / `--regular` / `--normal` | Uses the stable channel, aligning with release revisions and the 24-hour auto-upgrade cadence.【F:upgrade.sh†L249-L285】【F:upgrade.sh†L520-L550】 |
| `--clean` | Deletes `db.sqlite3` (and any `db_*.sqlite3` snapshots) after confirmation so migrations start from a blank database.【F:upgrade.sh†L122-L167】【F:upgrade.sh†L420-L444】 |
| `--start` / `--no-start` | Forces services to start after the upgrade (even if they were previously stopped) or keeps them offline afterwards; `--no-start` also accepts the legacy `--no-restart` alias.【F:upgrade.sh†L123-L144】【F:upgrade.sh†L404-L419】【F:upgrade.sh†L514-L551】 |
| `--no-warn` | Skips interactive confirmation before destructive database operations (used with `--clean` or uninstall flows).【F:upgrade.sh†L122-L167】 |

Additional behaviour:
- Before applying migrations it refreshes Nginx maintenance assets, optionally clears the database, reruns `env-refresh.sh`, migrates legacy systemd configurations, and restarts services unless `--no-start`/`--no-restart` was requested.【F:upgrade.sh†L404-L551】
- After restarting, it updates desktop shortcuts so GUI launchers stay current.【F:upgrade.sh†L552-L555】

### 3.2 Windows: `upgrade.bat`

The Windows upgrade helper focuses on Git safety and dependency refreshes. It pulls with rebase and reinstalls dependencies when the `requirements.txt` hash changes, using `scripts/helpers/pip_install.py` when available.【F:upgrade.bat†L1-L28】

## 4. Uninstallation

### 4.1 Linux: `uninstall.sh`

`uninstall.sh` removes system services, lock files, and the SQLite database. It warns before deleting persistent data unless you pass `--no-warn` (useful for scripted teardown). Providing `--service NAME` overrides the autodetected service from `.locks/service.lck`.【F:uninstall.sh†L1-L77】【F:uninstall.sh†L90-L159】 The script always prompts before stopping the server, disables any LCD and Celery units, cleans up WLAN watchdog services, and refreshes desktop shortcuts on exit.【F:uninstall.sh†L90-L167】

### 4.2 Windows

Windows nodes do not have a dedicated uninstaller. Remove the project directory manually when you no longer need it and delete the virtual environment folder if disk space matters.

---

**Quick reference**

1. Install (`install.sh` or `install.bat`).
2. Start (`start.sh` / `start.bat`) and stop (`stop.sh`).
3. Upgrade periodically (`upgrade.sh` / `upgrade.bat`).
4. Tear down with `uninstall.sh` on Linux.
