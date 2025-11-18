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
- [3. Upgrading (`upgrade.sh` and `upgrade.bat`)](#3-upgrading-upgradesh-and-upgradebat)
  - [3.1 Safe-upgrade features](#31-safe-upgrade-features)
  - [3.2 Linux upgrade flags](#32-linux-upgrade-flags)
  - [3.3 Windows upgrade workflow](#33-windows-upgrade-workflow)
- [4. Uninstalling (`uninstall.sh`)](#4-uninstalling-uninstallsh)
  - [4.1 Uninstall flags and prompts](#41-uninstall-flags-and-prompts)
  - [4.2 Cleanup performed](#42-cleanup-performed)

---

## 1. Installation scripts (`install.sh` and `install.bat`)

Run the installer from the project root. Every installer writes a timestamped log to `logs/install.log`, making it easy to review what happened if anything fails.【F:install.sh†L10-L16】【F:install.sh†L26-L30】 The shell variant also bootstraps helper tools shared with other scripts (logging, nginx maintenance, and desktop shortcut utilities).【F:install.sh†L6-L9】

### 1.1 Linux installer flags

`install.sh` accepts the flags below. Flags can be combined unless otherwise noted; unspecified options fall back to sensible defaults.

| Flag | Purpose |
| --- | --- |
| `--service NAME` | Registers the deployment under a specific systemd service name. When present, the installer records the value in `locks/service.lck`, enabling later scripts to control that service instead of spawning foreground processes.【F:install.sh†L18-L19】【F:install.sh†L150-L169】 |
| `--internal` / `--public` | Chooses the nginx topology. `--internal` listens on ports 8000/8080 for LAN-only access, while `--public` provisions TLS-forwarding on 80/443. Both modes proxy to the configured backend port (default 8888).【F:install.sh†L33-L52】【F:install.sh†L200-L296】 |
| `--port PORT` | Overrides the backend port used by Django when nginx proxies traffic. Defaults to 8888 for every role unless explicitly overridden.【F:install.sh†L57-L74】 |
| `--upgrade` | Runs the installer in upgrade mode, preserving state while refreshing configuration. Often paired with role flags to recompute dependencies.【F:install.sh†L75-L79】 |
| `--auto-upgrade` | Keeps unattended upgrades on (the default) and writes `locks/auto_upgrade.lck` so Celery schedules the checks.【F:install.sh†L190-L205】【F:install.sh†L559-L589】 |
| `--fixed` | Disables auto-upgrade and removes any existing automation lock so upgrades stay manual.【F:install.sh†L194-L205】【F:install.sh†L582-L589】 |
| `--unstable` / `--latest` / `--stable` / `--regular` / `--normal` | Selects the upgrade channel: unstable follows mainline revisions every 10 minutes; stable polls releases every 24 hours.【F:install.sh†L198-L205】【F:install.sh†L232-L275】【F:core/auto_upgrade.py†L10-L20】 |
| `--celery` | Forces Celery worker support even when the chosen role would normally skip it. The installer writes `locks/celery.lck` so later scripts manage the worker lifecycle.【F:install.sh†L87-L89】【F:install.sh†L170-L182】 |
| `--lcd-screen` / `--no-lcd-screen` | Controls LCD support. `--lcd-screen` installs required I²C packages (if missing) and records the feature lock, while `--no-lcd-screen` removes the lock so the display stays off.【F:install.sh†L90-L110】【F:install.sh†L183-L199】 |
| `--clean` | Deletes an existing SQLite database after first backing it up with a timestamp that includes the git revision. Use this when reinstalling on a development machine and you do not need existing data.【F:install.sh†L111-L152】 |
| `--start` | Immediately runs `start.sh` after installation completes so the services come up without a separate command.【F:install.sh†L112-L115】【F:install.sh†L307-L309】 |
| `--satellite`, `--terminal`, `--control`, `--watchtower` | High-level presets that bundle multiple flags and dependency checks for each node role. See [Role presets](#12-role-presets). |

Most flags only tweak configuration files and lock states; they do not persist secrets or environment variables. Review the generated `.env` files or rerun the installer with `--clean` when you need a fresh database snapshot.

### 1.2 Role presets

Role flags set opinionated defaults and verify external dependencies before proceeding. Control and Satellite builds must also
run on Ubuntu 22.04 or later with an `eth0` interface present before you install or rerun `switch-role.sh` to change into one
of those roles.

- **`--satellite`** – Requires nginx and Redis to be installed and running. Enables auto-upgrades on the stable channel, internal nginx, Celery, and marks the node as `Satellite`. Redis connection details are written to `redis.env`.【F:install.sh†L232-L240】【F:install.sh†L320-L373】
- **`--terminal`** – The lightest profile. Keeps nginx internal, targets the unstable channel, and enables Celery for background tasks while leaving auto-upgrade enabled by default.【F:install.sh†L243-L250】【F:install.sh†L320-L373】
- **`--control`** – For lab control stations. Requires nginx and Redis, enables auto-upgrades on the unstable channel, Celery, LCD control, and writes the `control.lck` flag so future scripts manage the accessory services.【F:install.sh†L252-L265】【F:install.sh†L320-L373】
- **`--watchtower`** – Cloud-oriented role. Requires nginx, flips nginx into public mode, enables auto-upgrades and Celery on the stable track, and records the `Watchtower` role for downstream tooling.【F:install.sh†L266-L275】【F:install.sh†L320-L373】

During installation, the script ensures the Python virtual environment exists, seeds nginx fallback assets, and writes fully rendered nginx vhosts (public or internal) with the correct upstream port substitution.【F:install.sh†L170-L309】 System prompts appear when prerequisites (nginx or Redis) are missing, explaining how to install them on Debian/Ubuntu systems.【F:install.sh†L33-L74】【F:install.sh†L124-L156】

### 1.3 Windows installer behaviour

`install.bat` mirrors the Linux workflow in a streamlined fashion. It creates `.venv` if necessary, upgrades `pip`, then installs every requirement—delegating to `scripts/helpers/pip_install.py` when available for consistent hashing. Finally, it runs `manage.py migrate` and refreshes environment metadata via `env-refresh.bat --latest`. No command-line flags are supported; the batch file always provisions the Terminal role defaults.【F:install.bat†L1-L21】

## 2. Starting and stopping services

### 2.1 Linux start options

`start.sh` is aware of the locks written by the installer. If a systemd service name is registered, it restarts that service (and any associated LCD or Celery units) instead of launching new foreground processes.【F:start.sh†L36-L69】 When running in foreground mode it performs these steps:

1. Validates that `.venv` exists and activates it.【F:start.sh†L12-L19】
2. Loads any `*.env` files into the process environment.【F:start.sh†L20-L27】
3. Computes an MD5 hash of collected static files, only running `collectstatic` when assets have changed.【F:start.sh†L28-L47】
4. Parses command-line options (described below).【F:start.sh†L71-L103】
5. Optionally starts Celery worker and beat processes in the background when enabled (default).【F:start.sh†L105-L116】
6. Runs Django’s development server on the requested interface/port, optionally with autoreload.【F:start.sh†L118-L125】

Available options:

| Option | Effect |
| --- | --- |
| `--port PORT` | Overrides the listening port (default 8888 regardless of nginx mode, matching the installer default).【F:start.sh†L86-L97】 |
| `--reload` | Enables Django’s autoreload loop for development scenarios. Default is `--noreload` for stability on appliance nodes.【F:start.sh†L98-L103】【F:start.sh†L119-L125】 |
| `--celery` / `--no-celery` | Forces Celery workers on or off regardless of locks. Celery is enabled by default to handle queued tasks like email delivery.【F:start.sh†L99-L104】【F:start.sh†L105-L116】 |
| `--public` / `--internal` | Convenience shorthands that reset the port to the installer default (8888) without touching nginx configuration. Handy when experimenting without rerunning the installer.【F:start.sh†L98-L103】 |

### 2.2 Stopping services on Linux

`stop.sh` complements `start.sh` by reversing the launch process. If a systemd service was registered it stops that unit (plus any Celery or LCD companions), showing `systemctl status` after each action for quick diagnostics.【F:stop.sh†L18-L48】 When running without systemd it:

- Activates the virtual environment when present for Python access.【F:stop.sh†L53-L57】
- Accepts an optional port or the `--all` flag. Without arguments it stops only the `runserver` instance bound to the default port; `--all` terminates every matching `manage.py runserver` process.【F:stop.sh†L59-L79】
- Kills background Celery processes started by `start.sh` and waits until they exit cleanly before finishing.【F:stop.sh†L80-L104】
- Sends a “Goodbye!” toast to the LCD screen when that accessory is enabled.【F:stop.sh†L106-L114】

### 2.3 Windows start workflow

`start.bat` follows the same pattern with fewer switches. It verifies `.venv`, performs the static hash optimisation, and runs `manage.py runserver` with `--noreload` unless `--reload` is provided. The only supported options are `--port PORT` and `--reload`; other arguments cause a usage hint and the script exits with an error.【F:start.bat†L1-L55】 Stop the Windows server with `Ctrl+C` in the same console—there is no dedicated `stop.bat`.

## 3. Upgrading (`upgrade.sh` and `upgrade.bat`)

### 3.1 Safe-upgrade features

Both upgrade scripts prioritise recoverability before applying new code:

- They create `failover-YYYYMMDD-N` branches that capture the current working tree (including uncommitted changes) so you can revert later.【F:upgrade.sh†L28-L96】【F:upgrade.sh†L200-L216】
- SQLite databases are copied into `backups/` alongside the failover branch name.【F:upgrade.sh†L97-L121】
- When reverting, the script restores both the git state and the database snapshot if available.【F:upgrade.sh†L217-L292】

### 3.2 Linux upgrade flags

`upgrade.sh` exposes several controls to tune the process:

| Flag | Purpose |
| --- | --- |
| `--latest` / `--unstable` | Follows origin/main revisions even when the recorded `VERSION` matches, matching the 10-minute unstable cadence.【F:upgrade.sh†L249-L285】【F:upgrade.sh†L520-L550】 |
| `--stable` / `--regular` / `--normal` | Uses the release-driven stable channel with 24-hour polling and revision matching.【F:upgrade.sh†L249-L285】【F:upgrade.sh†L520-L550】 |
| `--clean` | Removes untracked files (except `data/`), resets local changes, and keeps git history aligned—useful for appliance roles where local edits should be discarded.【F:upgrade.sh†L60-L94】【F:upgrade.sh†L146-L159】 |
| `--no-restart` | Skips restarting services after migration so you can review changes manually before bringing the node back online.【F:upgrade.sh†L123-L152】【F:upgrade.sh†L340-L363】 |
| `--no-warn` | Suppresses interactive warnings when an action would remove databases without creating a new backup (used together with `--clean` or manual purges).【F:upgrade.sh†L160-L201】 |

During a normal upgrade the script determines the node role, ensures no interrupted git operations are pending, updates dependencies when `requirements.txt` changes, applies Django migrations, and restarts services unless `--no-restart` was passed.【F:upgrade.sh†L33-L205】【F:upgrade.sh†L332-L419】

### 3.3 Windows upgrade workflow

`upgrade.bat` pulls the latest changes and refreshes Python dependencies when the MD5 hash of `requirements.txt` changes, using `scripts/helpers/pip_install.py` when present.【F:upgrade.bat†L1-L28】

## 4. Uninstalling (`uninstall.sh`)

Windows nodes reuse Add/Remove Programs, so only the Linux script is provided.

### 4.1 Uninstall flags and prompts

`uninstall.sh` offers two optional flags:

| Flag | Purpose |
| --- | --- |
| `--service NAME` | Overrides the service name recorded during installation. When omitted the script falls back to the value stored in `locks/service.lck`, if present.【F:uninstall.sh†L12-L45】 |
| `--no-warn` | Skips the confirmation prompt shown before deleting SQLite databases. Use cautiously in automation where no interactive approval is possible.【F:uninstall.sh†L17-L37】 |

The script always asks for confirmation before proceeding because the server will stop and local data may be removed.【F:uninstall.sh†L47-L60】

### 4.2 Cleanup performed

During removal the script:

1. Stops and disables any recorded systemd service, along with linked LCD and Celery units, then clears the associated lock files.【F:uninstall.sh†L61-L109】
2. Stops historical Wi-Fi watchdog services (`wlan1-refresh`, `wlan1-device-refresh`, `wifi-watchdog`) when they exist so nothing keeps touching network interfaces after the uninstall.【F:uninstall.sh†L110-L121】
3. Terminates any remaining `manage.py runserver` or Celery processes.【F:uninstall.sh†L122-L127】
4. Deletes `db.sqlite3`, removes the entire `locks/` directory, and clears the cached requirements hash so future installs start cleanly.【F:uninstall.sh†L129-L142】

Afterwards the script prints “Uninstall complete.” so you can safely remove the project directory or clone a fresh copy before reinstalling.【F:uninstall.sh†L140-L142】
