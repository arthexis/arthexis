# Arthexis Constellation

## Purpose

Arthexis Constellation is a [narrative-driven](https://en.wikipedia.org/wiki/Narrative) [Django](https://www.djangoproject.com/)-based [software suite](https://en.wikipedia.org/wiki/Software_suite) that centralizes tools for managing [electric vehicle charging infrastructure](https://en.wikipedia.org/wiki/Charging_station) and orchestrating [energy](https://en.wikipedia.org/wiki/Energy)-related [products](https://en.wikipedia.org/wiki/Product_(business)) and [services](https://en.wikipedia.org/wiki/Service_(economics)).

## Features

- Compatible with the [Open Charge Point Protocol (OCPP) 1.6](https://www.openchargealliance.org/protocols/ocpp-16/) central system, handling:
  - BootNotification
  - Heartbeat
  - StatusNotification
  - Authorize
  - MeterValues
  - DiagnosticsStatusNotification
  - StartTransaction
  - StopTransaction
  - FirmwareStatusNotification
- [API](https://en.wikipedia.org/wiki/API) integration with [Odoo](https://www.odoo.com/), syncing:
  - Employee credentials via `res.users`
  - Product catalog lookups via `product.product`
- Runs on [Windows 11](https://www.microsoft.com/windows/windows-11) and [Ubuntu 22.04 LTS](https://releases.ubuntu.com/22.04/)
- Tested for the [Raspberry Pi 4 Model B](https://www.raspberrypi.com/products/raspberry-pi-4-model-b/)

Project under active development.

## Role Architecture

Arthexis Constellation ships in four node roles tailored to different deployment scenarios.

| Role | Description & Common Features |
| --- | --- |
| Terminal | Single-User Research & Development<br>Features: GUI Toast |
| Control | Single-Device Testing & Special Task Appliances<br>Features: AP Public Wi-Fi, Celery Queue, GUI Toast, LCD Screen, NGINX Server, RFID Scanner |
| Satellite | Multi-Device Edge, Network & Data Acquisition<br>Features: AP Router, Celery Queue, NGINX Server, RFID Scanner |
| Constellation | Multi-User Cloud & Orchestration<br>Features: Celery Queue, NGINX Server |

## Quick Guide

### 1. Clone
- **[Linux](https://en.wikipedia.org/wiki/Linux)**: open a [terminal](https://en.wikipedia.org/wiki/Command-line_interface) and run  
  `git clone https://github.com/arthexis/arthexis.git`
- **[Windows](https://en.wikipedia.org/wiki/Microsoft_Windows)**: open [PowerShell](https://learn.microsoft.com/powershell/) or [Git Bash](https://gitforwindows.org/) and run the same command.

### 2. Start and stop
Terminal nodes can start directly with the scripts below without installing; Control, Satellite, and Constellation roles require installation first. Both approaches listen on [`http://localhost:8888/`](http://localhost:8888/) by default—pass `--port` to use a different value, or add `--public` to expose the service on `0.0.0.0:8000` so other devices can reach it (for example `http://192.168.1.10:8000/`). Constellation installations enable public mode automatically.
- **[VS Code](https://code.visualstudio.com/)**: open the folder, go to the
  **Run and Debug** panel (`Ctrl+Shift+D`), select the **Run Server** (or
  **Debug Server**) configuration, and press the green start button. Stop the
  server with the red square button (`Shift+F5`).
- **[Shell](https://en.wikipedia.org/wiki/Shell_(computing))**: on Linux run [`./start.sh`](start.sh) and stop with [`./stop.sh`](stop.sh); on Windows run [`start.bat`](start.bat) and stop with `Ctrl+C`.

### 3. Install and upgrade
- **Linux**: run [`./install.sh`](install.sh) with a node role flag:
  - `--terminal` – default when unspecified and recommended if you're unsure. Terminal nodes can also use the start/stop scripts above without installing.
  - `--control` – prepares the single-device testing appliance.
  - `--satellite` – configures the edge data acquisition node.
  - `--constellation` – enables the multi-user orchestration stack.
  Use `./install.sh --help` to list every available flag if you need to customize the node beyond the role defaults. Upgrade with [`./upgrade.sh`](upgrade.sh).
  Constellation installations run in public mode on port 8000; Terminal, Control, and Satellite default to the internal port 8888 unless you pass `--public` or override the port.
- **Windows**: run [`install.bat`](install.bat) to install (Terminal role) and [`upgrade.bat`](upgrade.bat) to upgrade.

### 4. Administration
Visit [`http://localhost:8888/admin/`](http://localhost:8888/admin/) for the [Django admin](https://docs.djangoproject.com/en/stable/ref/contrib/admin/) and [`http://localhost:8888/admindocs/`](http://localhost:8888/admindocs/) for the [admindocs](https://docs.djangoproject.com/en/stable/ref/contrib/admin/admindocs/). If you've enabled public mode (either manually with `--public` or by installing the Constellation role), open [`http://localhost:8000/`](http://localhost:8000/) on the host itself or replace `localhost` with the machine's IP when connecting from another device. Use `--port` with the start scripts or installer when you need to expose a different port.

## Support

Contact us at [tecnologia@gelectriic.com](mailto:tecnologia@gelectriic.com) or visit our [web page](https://www.gelectriic.com/) for [professional services](https://en.wikipedia.org/wiki/Professional_services) and [commercial support](https://en.wikipedia.org/wiki/Technical_support).

## About Me

> "What, you want to know about me too? Well, I enjoy [developing software](https://en.wikipedia.org/wiki/Software_development), [role-playing games](https://en.wikipedia.org/wiki/Role-playing_game), long walks on the [beach](https://en.wikipedia.org/wiki/Beach) and a fourth secret thing."
> --Arthexis

