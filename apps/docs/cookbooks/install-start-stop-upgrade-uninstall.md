# Arthexis Deployment Script Manual

This guide explains how to install, operate, upgrade, and remove an Arthexis node using the provided shell (`*.sh`) and batch (`*.bat`) scripts. Follow the sections in order when preparing a new system.

- [1. Installation scripts (`install.sh` and `install.bat`)](#1-installation-scripts-installsh-and-installbat)
  - [1.1 Linux installer flags](#11-linux-installer-flags)
  - [1.2 Role presets](#12-role-presets)
  - [1.3 Windows installer behaviour](#13-windows-installer-behaviour)
- [2. Starting and stopping services](#2-starting-and-stopping-services)
  - [2.1 Linux start options](#21-linux-start-options)
  - [2.2 Stopping services on Linux](#22-stopping-services-on-linux)
  - [2.3 Windows start workflow](#23-windows-start-workflow)
  - [2.4 Post-deploy proxy and security header checks](#24-post-deploy-proxy-and-security-header-checks)
- [3. Upgrading (`upgrade.sh` and `upgrade.bat`)](#3-upgrading-upgradesh-and-upgradebat)
  - [3.1 Safe-upgrade features](#31-safe-upgrade-features)
  - [3.2 Linux upgrade flags](#32-linux-upgrade-flags)
  - [3.3 Windows upgrade workflow](#33-windows-upgrade-workflow)
- [4. Uninstalling (`uninstall.sh`)](#4-uninstalling-uninstallsh)
  - [4.1 Uninstall flags and prompts](#41-uninstall-flags-and-prompts)
  - [4.2 Cleanup performed](#42-cleanup-performed)

---

## 1. Installation scripts (`install.sh` and `install.bat`)

Run the installer from the project root. Every installer writes a timestamped log to `logs/install.log`, making it easy to review what happened if anything fails. The shell variant also bootstraps helper tools shared with other scripts (logging and desktop shortcut utilities).

### 1.1 Linux installer flags

`install.sh` accepts the flags below. Flags can be combined unless otherwise noted; unspecified options fall back to sensible defaults.

| Flag | Purpose |
| --- | --- |
| `--service NAME` | Registers the deployment under a specific systemd service name. When present, the installer records the value in `.locks/service.lck`, enabling later scripts to control that service instead of spawning foreground processes. |
| `--port PORT` | Overrides the backend port used by Django. Defaults to 8888 for every role unless explicitly overridden. |
| `--upgrade` | Runs the installer in upgrade mode, preserving state while refreshing configuration. Often paired with role flags to recompute dependencies. |
| `--auto-upgrade` | Enables unattended upgrades (disabled by default) and writes `.locks/auto_upgrade.lck` so Celery schedules the checks. |
| `--fixed` | Leaves auto-upgrade off and removes any existing automation lock so upgrades stay manual. |
| `--unstable` / `--latest` / `--stable` / `--regular` / `--normal` | Enables auto-upgrade on the selected channel: unstable follows mainline revisions every 15 minutes; latest polls mainline revisions every hour; stable polls releases every 24 hours. |
| `--celery` | Forces Celery worker support even when the chosen role would normally skip it. The installer writes `.locks/celery.lck` so later scripts manage the worker lifecycle. |
| `--lcd-screen` / `--no-lcd-screen` | Controls LCD support. `--lcd-screen` installs required I²C packages (if missing) and records the feature lock, while `--no-lcd-screen` removes the lock so the display stays off. |
| `--rfid-service` / `--no-rfid-service` | Controls the always-on RFID scanner service. Enable it to install the `rfid-<service>` systemd unit, or disable it to remove the unit and lock file on role changes. |
| `--clean` | Deletes an existing SQLite database after first backing it up with a timestamp that includes the git revision. Use this when reinstalling on a development machine and you do not need existing data. |
| `--start` / `--no-start` | Runs (or skips) `start.sh` after installation completes so services come up automatically when desired. |
| `--satellite`, `--terminal`, `--control`, `--watchtower` | High-level presets that bundle multiple flags and dependency checks for each node role. See [Role presets](#12-role-presets). |

Most flags only tweak configuration files and lock states; they do not persist secrets or environment variables. Review the generated `.env` files or rerun the installer with `--clean` when you need a fresh database snapshot.

### 1.2 Role presets

Role flags set opinionated defaults and verify external dependencies before proceeding. Control and Satellite builds must also
run on Ubuntu 22.04 or later with an `eth0` interface present before you install or rerun `./configure.sh` to change into one
of those roles.

- **`--satellite`** – Enables Celery, marks the node as `Satellite`, and writes Redis connection details to `redis.env`; add `--stable` or `--unstable` to opt into auto-upgrades.
- **`--terminal`** – The lightest profile. Enables Celery for background tasks and defaults to fixed upgrades; choose `--unstable`/`--latest` or `--stable` to enable auto-upgrade on your preferred channel.
- **`--control`** – For lab control stations. Enables Celery, LCD control, and writes the `control.lck` flag so future scripts manage the accessory services. Starts services immediately unless you pass `--no-start`, and defaults to fixed upgrades unless you select a channel with `--unstable`/`--latest` or `--stable`.
- **`--watchtower`** – Cloud-oriented role. Enables Celery, records the `Watchtower` role for downstream tooling, and supports the same auto-upgrade options as the other presets.

During installation, the script ensures the Python virtual environment exists and prepares application metadata and dependencies. Nginx configuration is now managed exclusively through `apps.nginx` tooling rather than the lifecycle shell scripts. System prompts appear when prerequisites such as Redis are missing, explaining how to install them on Debian/Ubuntu systems.

### 1.3 Windows installer behaviour

`install.bat` mirrors the Linux workflow in a streamlined fashion. It creates `.venv` if necessary, upgrades `pip`, then installs every requirement—delegating to `scripts/helpers/pip_install.py` when available for consistent hashing. Finally, it runs `manage.py migrate` and refreshes environment metadata via `env-refresh.bat --latest`. No command-line flags are supported; the batch file always provisions the Terminal role defaults.

## 2. Starting and stopping services

### 2.1 Linux start options

`start.sh` is aware of the locks written by the installer. If a systemd service name is registered, it restarts that service (and any associated LCD or Celery units) instead of launching new foreground processes. When running in foreground mode it performs these steps:

1. Validates that `.venv` exists and activates it.
2. Loads any `*.env` files into the process environment.
3. Computes an MD5 hash of collected static files, only running `collectstatic` when assets have changed.
4. Parses command-line options (described below).
5. Optionally starts Celery worker and beat processes in the background when enabled (default).
6. Runs Django’s development server on the requested interface/port, optionally with autoreload.

Available options:

| Option | Effect |
| --- | --- |
| `--port PORT` | Overrides the listening port (default 8888 regardless of nginx mode, matching the installer default). |
| `--reload` | Enables Django’s autoreload loop for development scenarios. Default is `--noreload` for stability on appliance nodes. |
| `--celery` / `--no-celery` | Forces Celery workers on or off regardless of locks. Celery is enabled by default to handle queued tasks like email delivery. |
| `--public` / `--internal` | Convenience shorthands that reset the port to the installer default (8888) without touching nginx configuration. Handy when experimenting without rerunning the installer. |

### 2.2 Stopping services on Linux

`stop.sh` complements `start.sh` by reversing the launch process. If a systemd service was registered it stops that unit (plus any Celery, LCD, and RFID companions), showing `systemctl status` after each action for quick diagnostics. When running without systemd it:

- Activates the virtual environment when present for Python access.
- Accepts an optional port or the `--all` flag. Without arguments it stops only the `runserver` instance bound to the default port; `--all` terminates every matching `manage.py runserver` process.
- Kills background Celery processes started by `start.sh` and waits until they exit cleanly before finishing.
- Sends a “Goodbye!” toast to the LCD screen when that accessory is enabled.

### 2.3 Windows start workflow

`start.bat` follows the same pattern with fewer switches. It verifies `.venv`, performs the static hash optimisation, and runs `manage.py runserver` with `--noreload` unless `--reload` is provided. The only supported options are `--port PORT` and `--reload`; other arguments cause a usage hint and the script exits with an error. Stop the Windows server with `Ctrl+C` in the same console—there is no dedicated `stop.bat`.

### 2.4 Post-deploy proxy and security header checks

Use this checklist after deploying the Constellation site to ensure the reverse proxy is setting proxy headers correctly and that CSP headers are present.

1. **Confirm the managed nginx config sets `X-Forwarded-Proto`.** The generated proxy block in `apps/nginx/config_utils.py` includes `proxy_set_header X-Forwarded-Proto $scheme;`. Validate the deployed file (default `/etc/nginx/sites-enabled/arthexis.conf`) still includes that line for the Constellation server block. You can render a preview without touching production using:
   - `python manage.py preview_nginx_config --ids <site_configuration_id>`
2. **Verify the production reverse proxy forwards HTTPS as expected.** From a host that can reach the deployed nginx or CDN, fetch headers and confirm the upstream sees `X-Forwarded-Proto: https` in access logs or application logs (see logging toggle below).
3. **Check CSP headers on the Constellation landing page.** Run:
   - `curl -I "https://arthexis.com/#constellation" | rg -i "content-security-policy"`

   The fragment (`#constellation`) is not sent to the server, so the response headers should match `https://arthexis.com/`. Expect to see the CSP header configured in nginx (currently `upgrade-insecure-requests; block-all-mixed-content`).
4. **Enable proxy-header logging when troubleshooting.** Set `LOG_X_FORWARDED_PROTO=true` in the production environment to log missing or unexpected `X-Forwarded-Proto` values in the `proxy_headers` logger. Disable it after investigation to avoid noisy logs.

## 3. Upgrading (`upgrade.sh` and `upgrade.bat`)

### 3.1 Safe-upgrade features

Both upgrade scripts prioritise recoverability before applying new code:

- They capture upgrade output in timestamped logs and record lifecycle events for visibility in admin reports.
- When local changes exist, the Linux upgrade script stashes them (including untracked files) before applying updates and restores them afterwards so developer edits are preserved.

### 3.2 Linux upgrade flags

`upgrade.sh` exposes several controls to tune the process:

| Flag | Purpose |
| --- | --- |
| `--latest` / `--unstable` | Follows origin/main revisions even when the recorded `VERSION` matches, matching the 10-minute unstable cadence. |
| `--stable` / `--regular` / `--normal` | Uses the release-driven stable channel with 24-hour polling and revision matching. |
| `--clean` | Removes untracked files (except `data/`), resets local changes, and keeps git history aligned—useful for appliance roles where local edits should be discarded. |
| `--start` / `--no-start` | Forces services to start after upgrade or keeps them offline afterwards; `--no-start` also accepts the legacy `--no-restart` alias. |
| `--no-warn` | Suppresses interactive warnings when an action would remove databases without creating a new backup (used together with `--clean` or manual purges). |

During a normal upgrade the script determines the node role, ensures no interrupted git operations are pending, updates dependencies when `requirements.txt` changes, applies Django migrations, and restarts services unless `--no-start`/`--no-restart` was passed.

### 3.3 Windows upgrade workflow

`upgrade.bat` pulls the latest changes and refreshes Python dependencies when the MD5 hash of `requirements.txt` changes, using `scripts/helpers/pip_install.py` when present.

## 4. Uninstalling (`uninstall.sh`)

Windows nodes reuse Add/Remove Programs, so only the Linux script is provided.

### 4.1 Uninstall flags and prompts

`uninstall.sh` offers optional flags:

| Flag | Purpose |
| --- | --- |
| `--service NAME` | Overrides the service name recorded during installation. When omitted the script falls back to the value stored in `.locks/service.lck`, if present. |
| `--no-warn` | Skips the confirmation prompt shown before deleting SQLite databases. Use cautiously in automation where no interactive approval is possible. |
| `--rfid-service` / `--no-rfid-service` | Controls whether the RFID systemd unit and lock file are removed alongside the rest of the stack (defaults to removing it). |

The script always asks for confirmation before proceeding because the server will stop and local data may be removed.

### 4.2 Cleanup performed

During removal the script:

1. Stops and disables any recorded systemd service, along with linked LCD, RFID, and Celery units, then clears the associated lock files.
2. Stops historical Wi-Fi watchdog services (`wlan1-refresh`, `wlan1-device-refresh`, `wifi-watchdog`) when they exist so nothing keeps touching network interfaces after the uninstall.
3. Terminates any remaining `manage.py runserver` or Celery processes.
4. Deletes `db.sqlite3`, removes the entire `.locks/` directory, and clears the cached requirements hash so future installs start cleanly.

Afterwards the script prints “Uninstall complete.” so you can safely remove the project directory or clone a fresh copy before reinstalling.
