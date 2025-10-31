# Install & Lifecycle Scripts Manual

This manual explains how to deploy, operate, and retire an Arthexis node with the platform's shell and batch helpers. It is organised in the order you typically run them: installation, runtime start/stop helpers, upgrades, and uninstallation. Each section includes all supported flags and highlights how the scripts interact with services, lock files, and other subsystems.

## 1. Installation helpers

### 1.1 Linux: `install.sh`

`install.sh` prepares a virtual environment, configures Nginx, seeds lock files, and optionally provisions systemd units. Run it from the repository root on a Debian/Ubuntu compatible host. The script stores its log in `logs/install.log` and exits on any error to prevent partial installations.【F:install.sh†L1-L39】【F:install.sh†L480-L563】

**Role presets**

Passing a role flag applies a curated bundle of options. Each preset still honours additional command-line overrides you append afterwards.【F:install.sh†L190-L243】

| Flag | Description |
| --- | --- |
| `--terminal` (default) | Local workstation profile. Leaves auto-upgrade disabled, uses the internal Nginx template, reserves port 8888 unless overridden, and enables Celery for email delivery.【F:install.sh†L198-L205】 |
| `--control` | Appliance profile. Requires Nginx and Redis, enables Celery, LCD, Control-specific locks, auto-upgrade (latest track), internal Nginx, and writes the `Control` role lock. Sets default service name `arthexis`.【F:install.sh†L207-L222】【F:install.sh†L270-L306】 |
| `--satellite` | Edge node profile. Requires Nginx and Redis, enables Celery, sets auto-upgrade to latest, and configures the internal Nginx template.【F:install.sh†L183-L194】 |
| `--watchtower` | Multi-tenant profile. Requires Nginx and Redis, keeps Celery on, switches to the public Nginx proxy with HTTPS expectations, and tracks stable releases unless overridden.【F:install.sh†L223-L241】 |
| `--constellation` | Deprecated alias for Watchtower; prints a warning but behaves identically so legacy tooling continues to work.【F:install.sh†L242-L252】 |

**General options**

| Flag | Purpose |
| --- | --- |
| `--service NAME` | Installs or updates systemd services (`NAME`, `celery-NAME`, `celery-beat-NAME`, and optionally `lcd-NAME`) and records the name in `locks/service.lck` for the runtime helpers.【F:install.sh†L133-L137】【F:install.sh†L480-L563】 |
| `--internal` | Forces the internal Nginx template (ports 8000/8080). This is the default unless a role preset changes it.【F:install.sh†L30-L36】【F:install.sh†L320-L373】 |
| `--public` | Enables the public HTTPS reverse proxy template and defaults the HTTP backend port to 8000.【F:install.sh†L37-L44】【F:install.sh†L305-L373】 |
| `--port PORT` | Overrides the backend Django port used in generated systemd units and the stored lock. If omitted, `8000` is used for public mode and `8888` for internal mode.【F:install.sh†L45-L60】 |
| `--upgrade` | Immediately runs `upgrade.sh` after installation (respecting the `--latest` / `--stable` track selection).【F:install.sh†L61-L68】【F:install.sh†L526-L551】 |
| `--auto-upgrade` | Schedules automatic upgrades via Celery, writes `locks/auto_upgrade.lck`, and optionally performs a first upgrade run.【F:install.sh†L20-L28】【F:install.sh†L526-L551】 |
| `--latest` | Switches the upgrade track to latest commits; conflicts with `--stable`. Used by presets that prioritise new features (Terminal/Satellite).【F:install.sh†L69-L76】【F:install.sh†L526-L551】 |
| `--stable` | Pins auto-upgrade and manual upgrade to the latest stable release on the current branch; incompatible with `--latest`.【F:install.sh†L69-L76】【F:install.sh†L524-L551】 |
| `--celery` | Forces Celery services on even if the preset would leave them disabled. Rarely needed because all presets already enable Celery.【F:install.sh†L162-L170】 |
| `--lcd-screen` / `--no-lcd-screen` | Adds or removes the LCD updater service and lock. Control preset enables it automatically; `--no-lcd-screen` removes an existing unit after reading `locks/service.lck`.【F:install.sh†L171-L189】【F:install.sh†L516-L563】 |
| `--clean` | Deletes `db.sqlite3` before installing, after first backing it up into `backups/` with version and Git metadata.【F:install.sh†L90-L120】 |
| `--start` | Launches `start.sh` after setup completes, which is useful for unattended provisioning.【F:install.sh†L121-L132】【F:install.sh†L554-L563】 |

The script also:

- Verifies Nginx and Redis availability for roles that require them, writing `redis.env` when Redis is configured.【F:install.sh†L78-L118】【F:install.sh†L248-L306】
- Creates `.venv`, installs dependencies via `scripts/helpers/pip_install.py`, applies migrations, and refreshes environment secrets via `env-refresh.sh`.【F:install.sh†L318-L476】
- Writes lock files capturing the selected role, Nginx mode, and enabled subsystems so the runtime helpers know how to behave.【F:install.sh†L248-L316】
- Refreshes desktop shortcuts on Linux desktops for quick access to the UI and maintenance commands.【F:install.sh†L564-L566】

### 1.2 Windows: `install.bat`

The Windows installer is intentionally simple: it bootstraps `.venv`, installs requirements, applies migrations, and then runs `env-refresh.bat --latest` so developers always start on the latest schema locally.【F:install.bat†L1-L20】 No flags are accepted—Windows nodes rely on Terminal defaults (development server at port 8000, no system services). Run `install.bat` again whenever dependencies change.

## 2. Runtime helpers

### 2.1 Linux: `start.sh`

`start.sh` activates the virtual environment, loads `*.env` files, optionally restarts provisioned systemd units, and then launches Django. Static assets are hashed before running `collectstatic`, saving time on repeated starts.【F:start.sh†L1-L66】【F:start.sh†L94-L143】 Supported options:

| Flag | Purpose |
| --- | --- |
| `--port PORT` | Overrides the serving port; defaults to `8000` when the Nginx mode lock is `public` and `8888` otherwise.【F:start.sh†L72-L112】 |
| `--reload` | Runs Django with auto-reload enabled (useful for development).【F:start.sh†L106-L117】【F:start.sh†L136-L145】 |
| `--celery` / `--no-celery` | Enables or disables the Celery worker/beat pair that handles background email. Defaults to on.【F:start.sh†L100-L145】 |
| `--public` / `--internal` | Convenience shorthands to force the default port to 8000 or 8888 without editing the lock file.【F:start.sh†L108-L119】 |

When a systemd service was previously installed, the script prefers restarting those units (plus `celery-*` and `lcd-*` companions) and exits once they are healthy. Otherwise it runs the development server in the foreground.【F:start.sh†L46-L101】 Celery processes launched directly are cleaned up automatically when the script exits because of the `trap` handler.【F:start.sh†L120-L145】

### 2.2 Linux: `stop.sh`

`stop.sh` mirrors the start helper: it stops systemd units when present, including Celery and LCD services, or falls back to killing `manage.py runserver` and Celery processes. It respects non-interactive sudo where available and displays service status for confirmation.【F:stop.sh†L1-L69】 Use:

- `stop.sh PORT` (default `8888`) to stop only the server bound to that port.【F:stop.sh†L70-L103】
- `stop.sh --all` to stop every matching runserver process regardless of port.【F:stop.sh†L70-L109】

On Control nodes with LCD support, it also sends a farewell notification before shutting down the display updater.【F:stop.sh†L32-L68】【F:stop.sh†L110-L129】

### 2.3 Windows: `start.bat`

The Windows starter mirrors the Linux workflow without service management: it validates `.venv`, reruns `collectstatic` only when the static hash changes, and starts Django at the requested port (default 8000). Flags: `--port PORT` and `--reload`. Any other argument prints usage and aborts.【F:start.bat†L1-L57】 Use `Ctrl+C` to stop the server.

## 3. Upgrades

### 3.1 Linux: `upgrade.sh`

`upgrade.sh` keeps nodes current while protecting local changes. Every run can create a failover branch with a matching SQLite backup so you can roll back using `--revert`. The script infers the node role from `locks/role.lck` to decide whether local commits should be discarded (Control/Watchtower nodes auto-align to the remote branch).【F:upgrade.sh†L15-L118】【F:upgrade.sh†L360-L404】

Supported options:

| Flag | Purpose |
| --- | --- |
| `--latest` | Forces an upgrade even when `VERSION` matches origin (useful when you track nightly commits). Cannot be combined with `--stable`.【F:upgrade.sh†L123-L158】 |
| `--stable` | Skips upgrades unless the remote release changes the major/minor version, mirroring the stable track used during installation. Incompatible with `--latest`.【F:upgrade.sh†L123-L158】 |
| `--clean` | Deletes `db.sqlite3` (and any `db_*.sqlite3` snapshots) after confirmation so migrations start from a blank database.【F:upgrade.sh†L122-L167】【F:upgrade.sh†L420-L444】 |
| `--no-restart` | Prevents the helper from stopping services or relaunching them afterwards. Handy when you only want to refresh the working tree.【F:upgrade.sh†L123-L144】【F:upgrade.sh†L404-L419】【F:upgrade.sh†L514-L551】 |
| `--revert` | Restores the latest failover branch and database backup, prompting if the database sizes differ significantly.【F:upgrade.sh†L118-L218】【F:upgrade.sh†L360-L404】 |
| `--no-warn` | Skips interactive confirmation before destructive database operations (used with `--clean` or uninstall flows).【F:upgrade.sh†L122-L167】 |

Additional behaviour:

- `upgrade.sh` aborts when it detects interrupted Git operations (rebase/merge/cherry-pick) so you do not lose work, then realigns Control/Watchtower branches by discarding local commits and untracked files.【F:upgrade.sh†L33-L118】【F:upgrade.sh†L203-L315】
- Failover branches follow the `failover-YYYYMMDD-N` naming scheme, preserving both code and database snapshots. Older failover branches are pruned after a successful run to avoid clutter.【F:upgrade.sh†L168-L315】
- The script fetches origin, compares `VERSION`, and exits early when already up to date unless `--latest` overrides. Stable mode only upgrades when the remote release leaves the current minor version.【F:upgrade.sh†L332-L404】
- Before applying migrations it refreshes Nginx maintenance assets, optionally clears the database, reruns `env-refresh.sh`, migrates legacy systemd configurations, and restarts services unless `--no-restart` was requested.【F:upgrade.sh†L404-L551】
- After restarting, it updates desktop shortcuts so GUI launchers stay current.【F:upgrade.sh†L552-L555】

### 3.2 Windows: `upgrade.bat`

The Windows upgrade helper focuses on Git safety and dependency refreshes. Every run creates a `failover-YYYYMMDD-N` branch (and copies `db.sqlite3` when present) before pulling with rebase. Requirement hashes are stored in `requirements.md5` so pip only runs when the dependency list changes.【F:upgrade.bat†L1-L48】 Use `upgrade.bat --revert` to reset to the newest failover branch and restore the saved database, with an interactive size check mirroring the Linux workflow.【F:upgrade.bat†L8-L71】

## 4. Uninstallation

### 4.1 Linux: `uninstall.sh`

`uninstall.sh` removes system services, lock files, and the SQLite database. It warns before deleting persistent data unless you pass `--no-warn` (useful for scripted teardown). Providing `--service NAME` overrides the autodetected service from `locks/service.lck`.【F:uninstall.sh†L1-L77】【F:uninstall.sh†L90-L159】 The script always prompts before stopping the server, disables any LCD and Celery units, cleans up WLAN watchdog services, and refreshes desktop shortcuts on exit.【F:uninstall.sh†L90-L167】

### 4.2 Windows

Windows nodes do not have a dedicated uninstaller. Remove the project directory manually when you no longer need it and delete the virtual environment folder if disk space matters.

---

**Quick reference**

1. Install (`install.sh` or `install.bat`).
2. Start (`start.sh` / `start.bat`) and stop (`stop.sh`).
3. Upgrade periodically (`upgrade.sh` / `upgrade.bat`).
4. Tear down with `uninstall.sh` on Linux.
