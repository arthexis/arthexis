# Arthexis Constellation

[![Coverage](https://raw.githubusercontent.com/arthexis/arthexis/main/coverage.svg)](https://github.com/arthexis/arthexis/actions/workflows/coverage.yml) [![OCPP 1.6 Coverage](https://raw.githubusercontent.com/arthexis/arthexis/main/ocpp_coverage.svg)](https://github.com/arthexis/arthexis/blob/main/docs/development/ocpp-user-manual.md)


## Purpose

Arthexis Constellation is a [narrative-driven](https://en.wikipedia.org/wiki/Narrative) [Django](https://www.djangoproject.com/)-based [software suite](https://en.wikipedia.org/wiki/Software_suite) that centralizes tools for managing [electric vehicle charging infrastructure](https://en.wikipedia.org/wiki/Charging_station) and orchestrating [energy](https://en.wikipedia.org/wiki/Energy)-related [products](https://en.wikipedia.org/wiki/Product_(business)) and [services](https://en.wikipedia.org/wiki/Service_(economics)).

## Current Features

- Compatible with the [Open Charge Point Protocol (OCPP) 1.6](https://www.openchargealliance.org/protocols/ocpp-16/) central system, handling:
  - Lifecycle & sessions
    - `BootNotification`
    - `Heartbeat`
    - `StatusNotification`
    - `StartTransaction`
    - `StopTransaction`
  - Access & metering
    - `Authorize`
    - `MeterValues`
  - Maintenance & firmware
    - `DiagnosticsStatusNotification`
    - `FirmwareStatusNotification`
- [API](https://en.wikipedia.org/wiki/API) integration with [Odoo](https://www.odoo.com/), syncing:
  - Employee credentials via `res.users`
  - Product catalog lookups via `product.product`
- Runs on [Windows 11](https://www.microsoft.com/windows/windows-11) and [Ubuntu 22.04 LTS](https://releases.ubuntu.com/22.04/)
- Tested for the [Raspberry Pi 4 Model B](https://www.raspberrypi.com/products/raspberry-pi-4-model-b/)

Project under rapid active and open development.

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
Terminal nodes can start directly with the scripts below without installing; Control, Satellite, and Watchtower roles require installation first. Both approaches listen on [`http://localhost:8000/`](http://localhost:8000/) by default.

- **[VS Code](https://code.visualstudio.com/)**
   - Open the folder and go to the **Run and Debug** panel (`Ctrl+Shift+D`).
   - Select the **Run Server** (or **Debug Server**) configuration.
   - Press the green start button. Stop the server with the red square button (`Shift+F5`).

- **[Shell](https://en.wikipedia.org/wiki/Shell_(computing))**
   - Linux: run [`./start.sh`](start.sh) and stop with [`./stop.sh`](stop.sh).
   - Windows: run [`start.bat`](start.bat) and stop with `Ctrl+C`.

### 3. Install and upgrade
- **Linux:**
   - Run [`./install.sh`](install.sh) with a node role flag:
     - `--terminal` – default when unspecified and recommended if you're unsure. Terminal nodes can also use the start/stop scripts above without installing.
     - `--control` – prepares the single-device testing appliance.
     - `--satellite` – configures the edge data acquisition node.
     - `--constellation` – enables the multi-user orchestration stack.
   - Use `./install.sh --help` to list every available flag if you need to customize the node beyond the role defaults.
   - Upgrade with [`./upgrade.sh`](upgrade.sh).

- **Windows:**
   - Run [`install.bat`](install.bat) to install (Terminal role) and [`upgrade.bat`](upgrade.bat) to upgrade.
   - Installation is not required to start in Terminal mode (the default).

### 4. Administration
Visit [`http://localhost:8000/admin/`](http://localhost:8000/admin/) for the [Django admin](https://docs.djangoproject.com/en/stable/ref/contrib/admin/) and [`http://localhost:8000/admindocs/`](http://localhost:8000/admindocs/) for the [admindocs](https://docs.djangoproject.com/en/stable/ref/contrib/admin/admindocs/). Use `--port` with the start scripts or installer when you need to expose a different port.

## Sigils

Sigils are bracketed tokens such as `[ENV.SMTP_PASSWORD]` that Arthexis expands at runtime. They make it possible to reference configuration secrets, system metadata, or records stored in other apps without duplicating values across the project.

### Syntax at a glance

- `[PREFIX.KEY]` &mdash; returns a field or attribute. Hyphens and casing are normalized automatically.
- `[PREFIX=IDENTIFIER.FIELD]` &mdash; selects a specific record by primary key or any unique field.
- `[PREFIX:FIELD=VALUE.ATTRIBUTE]` &mdash; filters by a custom field instead of the primary key.
- `[PREFIX.FIELD=[OTHER.SIGIL]]` &mdash; nests sigils so the value after `=` resolves before the outer token.
- `[PREFIX]` &mdash; for entity prefixes, returns the serialized object in JSON; for configuration prefixes, resolves to an empty string when the key is missing.

The platform ships with three configuration prefixes:

- `ENV` reads environment variables.
- `CONF` reads Django settings.
- `SYS` exposes computed system information such as build metadata.

Additional prefixes are defined through **Sigil Roots**, which map a short code (for example `ROLE`, `ODOO`, or `USER`) to a Django model. You can review them from **Admin &rarr; Sigil Builder** (`/admin/sigil-builder/`), where a test console is also available.

Unknown prefixes remain in place (e.g. `[UNKNOWN.VALUE]`) and are logged. When the optional `gway` CLI is installed, the resolver will attempt to delegate unresolved tokens to it before falling back to the original text.

## Support

Contact us at [tecnologia@gelectriic.com](mailto:tecnologia@gelectriic.com) or visit our [web page](https://www.gelectriic.com/) for [professional services](https://en.wikipedia.org/wiki/Professional_services) and [commercial support](https://en.wikipedia.org/wiki/Technical_support).

## Project Guidelines

- [AGENTS](AGENTS.md) – operating handbook for repository workflows, testing, and release management.
- [DESIGN](DESIGN.md) – visual, UX, and branding guidance that all interfaces must follow.

## About Me

> "What, you want to know about me too? Well, I enjoy [developing software](https://en.wikipedia.org/wiki/Software_development), [role-playing games](https://en.wikipedia.org/wiki/Role-playing_game), long walks on the [beach](https://en.wikipedia.org/wiki/Beach) and a fourth secret thing."
> --Arthexis

