# Constellation

[![CI](https://img.shields.io/github/actions/workflow/status/arthexis/arthexis/ci.yml?branch=main&label=CI&cacheSeconds=300)](https://github.com/arthexis/arthexis/actions/workflows/ci.yml) [![PyPI](https://img.shields.io/pypi/v/arthexis?label=PyPI)](https://pypi.org/project/arthexis/) [![OCPP 1.6 Coverage](https://raw.githubusercontent.com/arthexis/arthexis/main/media/ocpp_coverage.svg)](https://github.com/arthexis/arthexis/blob/main/docs/development/ocpp-user-manual.md) [![OCPP 2.0.1 Coverage](https://raw.githubusercontent.com/arthexis/arthexis/main/media/ocpp201_coverage.svg)](https://github.com/arthexis/arthexis/blob/main/docs/development/ocpp-user-manual.md) [![OCPP 2.1 Coverage](https://raw.githubusercontent.com/arthexis/arthexis/main/media/ocpp21_coverage.svg)](https://github.com/arthexis/arthexis/blob/main/docs/development/ocpp-user-manual.md) [![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE) ![Watchtowers](https://raw.githubusercontent.com/arthexis/arthexis/main/media/watchtowers.svg)


## Purpose

Arthexis Constellation is a [Django](https://www.djangoproject.com/)-based [software suite](https://en.wikipedia.org/wiki/Software_suite) that centralizes tools for managing [electric vehicle charging infrastructure](https://en.wikipedia.org/wiki/Charging_station) and orchestrating [energy](https://en.wikipedia.org/wiki/Energy)-related [products](https://en.wikipedia.org/wiki/Product_(business)) and [services](https://en.wikipedia.org/wiki/Service_(economics)).

Visit our [Changelog Report](https://arthexis.com/changelog/) to browse past and future features and other updates.

## Suite Features

- Compatible with the [Open Charge Point Protocol (OCPP) 1.6](https://www.openchargealliance.org/protocols/ocpp-16/) by default, while allowing Charging Stations to upgrade to newer protocols if they support them.

  **Charge point → CSMS**

  | Action | 1.6 | 2.0.1 | 2.1 | What we do |
  | --- | --- | --- | --- | --- |
  | `Authorize` | ✅ | ✅ | ✅ | Validate RFID or token authorization requests before a session starts. |
  | `BootNotification` | ✅ | ✅ | ✅ | Register the charge point and update identity, firmware, and status details. |
  | `DataTransfer` | ✅ | ✅ | ✅ | Accept vendor-specific payloads and record the results. |
  | `DiagnosticsStatusNotification` | ✅ | — | — | Track the progress of diagnostic uploads kicked off from the back office. |
  | `FirmwareStatusNotification` | ✅ | ✅ | ✅ | Track firmware update lifecycle events from charge points. |
  | `Heartbeat` | ✅ | ✅ | ✅ | Keep the websocket session alive and update last-seen timestamps. |
  | `LogStatusNotification` | — | ✅ | ✅ | Report log upload progress from the charge point for diagnostics oversight. |
  | `MeterValues` | ✅ | ✅ | ✅ | Persist periodic energy and power readings while a transaction is active. |
  | `SecurityEventNotification` | — | ✅ | ✅ | Record charge point security events for audit trails. |
  | `StartTransaction` | ✅ | — | — | Create charging sessions with initial meter values and identification data. |
  | `StatusNotification` | ✅ | ✅ | ✅ | Reflect connector availability and fault states in real time. |
  | `StopTransaction` | ✅ | — | — | Close charging sessions, capturing closing meter values and stop reasons. |

  **CSMS → Charge point**

  | Action | 1.6 | 2.0.1 | 2.1 | What we do |
  | --- | --- | --- | --- | --- |
  | `CancelReservation` | ✅ | ✅ | ✅ | Withdraw pending reservations and release connectors directly from the control center. |
  | `ChangeAvailability` | ✅ | ✅ | ✅ | Switch connectors or the whole station between operative and inoperative states. |
  | `ChangeConfiguration` | ✅ | — | — | Update supported charger settings and persist applied values in the control center. |
  | `ClearCache` | ✅ | ✅ | ✅ | Flush local authorization caches to force fresh lookups from the CSMS. |
  | `DataTransfer` | ✅ | ✅ | ✅ | Send vendor-specific commands and log the charge point response. |
  | `GetConfiguration` | ✅ | — | — | Poll the device for the current values of tracked configuration keys. |
  | `GetDiagnostics` | ✅ | — | — | Request a diagnostics archive upload to a signed URL for troubleshooting. |
  | `GetLocalListVersion` | ✅ | ✅ | ✅ | Retrieve the current RFID whitelist version and synchronize entries reported by the charge point. |
  | `RemoteStartTransaction` | ✅ | — | — | Initiate a charging session remotely for an identified customer or token. |
  | `RemoteStopTransaction` | ✅ | — | — | Terminate active charging sessions from the control center. |
  | `ReserveNow` | ✅ | ✅ | ✅ | Reserve connectors for upcoming sessions with automatic connector selection and confirmation tracking. |
  | `Reset` | ✅ | ✅ | ✅ | Request a soft or hard reboot to recover from faults. |
  | `SendLocalList` | ✅ | ✅ | ✅ | Publish released and approved RFIDs as the charge point's local authorization list. |
  | `TriggerMessage` | ✅ | ✅ | ✅ | Ask the device to send an immediate update (for example status or diagnostics). |
  | `UnlockConnector` | ✅ | ✅ | ✅ | Release stuck connectors without on-site intervention. |
  | `UpdateFirmware` | ✅ | ✅ | ✅ | Deliver firmware packages to chargers with secure download tokens and track installation responses. |

## Role Architecture

Arthexis Constellation ships in four node roles tailored to different deployment scenarios.

<table border="1" cellpadding="8" cellspacing="0">
  <thead>
    <tr>
      <th align="left">Role</th>
      <th align="left">Description &amp; Common Features</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td valign="top"><strong>Terminal</strong></td>
      <td valign="top"><strong>Single-User Research &amp; Development</strong><br />Features: GUI Toast</td>
    </tr>
    <tr>
      <td valign="top"><strong>Control</strong></td>
      <td valign="top"><strong>Single-Device Testing &amp; Special Task Appliances</strong><br />Features: AP Public Wi-Fi, Celery Queue, GUI Toast, LCD Screen, NGINX Server, RFID Scanner</td>
    </tr>
    <tr>
      <td valign="top"><strong>Satellite</strong></td>
      <td valign="top"><strong>Multi-Device Edge, Network &amp; Data Acquisition</strong><br />Features: AP Router, Celery Queue, NGINX Server, RFID Scanner</td>
    </tr>
    <tr>
      <td valign="top"><strong>Watchtower</strong></td>
      <td valign="top"><strong>Multi-User Cloud &amp; Orchestration</strong><br />Features: Celery Queue, NGINX Server</td>
    </tr>
  </tbody>
</table>

## Quick Guide

### 1. Clone
- **[Linux](https://en.wikipedia.org/wiki/Linux)**: open a [terminal](https://en.wikipedia.org/wiki/Command-line_interface) and run `git clone https://github.com/arthexis/arthexis.git`.
- **[Windows](https://en.wikipedia.org/wiki/Microsoft_Windows)**: open [PowerShell](https://learn.microsoft.com/powershell/) or [Git Bash](https://gitforwindows.org/) and run the same command.

### 2. Start and stop
Terminal nodes can start directly with the scripts below without installing; Control, Satellite, and Watchtower roles require installation first. Both approaches listen on `localhost:8888` by default.

For local bootstrapping, run `./install.sh --terminal` to install with defaults, start the server with `./start.sh` (optionally passing `--reload` or `--celery`), and execute a quick smoke test with `pytest -k smoke`. Override the role, port, reload, Celery, and test selection with the script flags when needed.

- **[VS Code](https://code.visualstudio.com/)**
   - Open the folder and go to the **Run and Debug** panel (`Ctrl+Shift+D`).
   - Select the **Run Server** (or **Debug Server**) configuration.
   - Press the green start button. Stop the server with the red square button (`Shift+F5`).

- **[Shell](https://en.wikipedia.org/wiki/Shell_(computing))**
   - Linux: run [`./start.sh`](start.sh) and stop with [`./stop.sh`](stop.sh).
   - Windows: run [`start.bat`](start.bat) and stop with `Ctrl+C`.

### 3. Install and upgrade
- **Linux:**
  - Run `./install.sh --terminal` to bootstrap a default Terminal node (pass `--control`, `--satellite`, or `--watchtower` as needed). Override the port with `--port 8888` (the default fallback) and control Celery with `--celery` or `--no-celery`. Use `./install.sh --help` to see every available option.
  - Use [`./upgrade.sh`](upgrade.sh) with `--stable` (weekly) or `--latest`/`-l`/`--unstable` (daily) to follow the preferred release cadence.
   - Consult the [Install & Lifecycle Scripts Manual](docs/development/install-lifecycle-scripts-manual.md) for complete flag descriptions and operational notes.
   - Review the [Auto-Upgrade Flow](docs/auto-upgrade.md) for how delegated upgrades run and how to observe them.

- **Windows:**
   - Run [`install.bat`](install.bat) to install (Terminal role) and [`upgrade.bat`](upgrade.bat) to upgrade.
   - Installation is not required to start in Terminal mode (the default).

Upgrade channels (opt-in during install/upgrade or with `scripts/delegated-upgrade.sh`):

| Channel | Check cadence | Purpose | Opt-in flag |
| --- | --- | --- | --- |
| Stable | Weekly (Thu before 5:00 AM) | Tracks release revisions with automated weekly checks. | `--stable` (default) |
| Latest | Daily (same hour) | Follows the newest mainline revisions with daily checks. | `--latest` / `-l` or `--unstable` |
| Manual | None (manual upgrades only) | Disables the automatic upgrade loop for full operator control. | _Run upgrades on demand without specifying a channel flag._ |

### 4. Administration
- Access the [Django admin](https://docs.djangoproject.com/en/stable/ref/contrib/admin/) at `localhost:8888/admin/` to review and manage live data. Use `--port` with the start scripts or installer when you need to expose a different port.
- Browse the [admindocs](https://docs.djangoproject.com/en/stable/ref/contrib/admin/admindocs/) at `localhost:8888/admindocs/` for API documentation that is generated from your models.
- Follow the [Install & Administration Guide](apps/docs/cookbooks/install-start-stop-upgrade-uninstall.md) for deployment, lifecycle tasks, and operational runbooks.
- Onboard and service chargers with the [EVCS Connectivity & Maintenance Cookbook](apps/docs/cookbooks/evcs-connectivity-maintenance.md).
- Configure payment gateways with the [Payment Processors Cookbook](apps/docs/cookbooks/payment-processors.md).
- Reference the [Sigils Cookbook](apps/docs/cookbooks/sigils.md) when configuring tokenized settings across environments.
- Understand seed fixtures and per-user files with [Managing Local Node Data](apps/docs/cookbooks/managing-local-node-data.md).
- Manage exports, imports, and audit trails with the [User Data Cookbook](apps/docs/cookbooks/user-data.md).
- Plan feature rollout strategies using the [Node Features Cookbook](apps/docs/cookbooks/node-features.md).
- Curate shortcuts for power users through the [Favorites Cookbook](apps/docs/cookbooks/favorites.md).
- Connect Slack workspaces through the [Slack Bot Onboarding Cookbook](apps/docs/cookbooks/slack-bot-onboarding.md).

## Support

Arthexis Constellation is still under very active development and new features are added every day.

If you decide to use our suite for your energy projects, you may contact us at [tecnologia@gelectriic.com](mailto:tecnologia@gelectriic.com) or visit our [web page](https://www.gelectriic.com/) for [professional services](https://en.wikipedia.org/wiki/Professional_services) and [commercial support](https://en.wikipedia.org/wiki/Technical_support).

## About Me

> "What, you want to know about me too? Well, I enjoy [developing software](https://en.wikipedia.org/wiki/Software_development), [role-playing games](https://en.wikipedia.org/wiki/Role-playing_game), long walks on the [beach](https://en.wikipedia.org/wiki/Beach) and a fourth secret thing above all else."
> --Arthexis
