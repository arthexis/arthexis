# Arthexis Constellation

## Purpose

Arthexis Constellation is a [narrative-driven](https://en.wikipedia.org/wiki/Narrative) [Django](https://www.djangoproject.com/)-based [software suite](https://en.wikipedia.org/wiki/Software_suite) that centralizes tools for managing [electric vehicle charging infrastructure](https://en.wikipedia.org/wiki/Charging_station) and orchestrating [energy](https://en.wikipedia.org/wiki/Energy)-related [products](https://en.wikipedia.org/wiki/Product_(business)) and [services](https://en.wikipedia.org/wiki/Service_(economics)).

## Features

- Compatible with the [Open Charge Point Protocol (OCPP) 1.6](https://www.openchargealliance.org/protocols/ocpp-16/)
- [API](https://en.wikipedia.org/wiki/API) integration with [Odoo](https://www.odoo.com/) 1.6
- Runs on [Windows 11](https://www.microsoft.com/windows/windows-11) and [Ubuntu 22.04 LTS](https://releases.ubuntu.com/22.04/)
- Tested for the [Raspberry Pi 4 Model B](https://www.raspberrypi.com/products/raspberry-pi-4-model-b/)
- CONTINUOUS RELEASE

## Four Role Architecture

Arthexis Constellation ships in four node roles tailored to different deployment scenarios.

| Role | Description | Common features |
| --- | --- | --- |
| Terminal | Single-User Research & Development | • GUI Toast |
| Control | Single-Device Testing & Special Task Appliances | • AP Public Wi-Fi<br>• Celery Queue<br>• GUI Toast<br>• LCD Screen<br>• NGINX Server<br>• RFID Scanner |
| Satellite | Multi-Device Edge, Network & Data Acquisition | • AP Router<br>• Celery Queue<br>• NGINX Server<br>• RFID Scanner |
| Constellation | Multi-User Cloud & Orchestration | • Celery Queue<br>• NGINX Server |

## Quick Guide

### 1. Clone
- **[Linux](https://en.wikipedia.org/wiki/Linux)**: open a [terminal](https://en.wikipedia.org/wiki/Command-line_interface) and run  
  `git clone https://github.com/arthexis/arthexis.git`
- **[Windows](https://en.wikipedia.org/wiki/Microsoft_Windows)**: open [PowerShell](https://learn.microsoft.com/powershell/) or [Git Bash](https://gitforwindows.org/) and run the same command.

### 2. Start and stop
- **[VS Code](https://code.visualstudio.com/)**: open the folder and run  
  `python [vscode_manage.py](vscode_manage.py) runserver`; press `Ctrl+C` to stop.
- **[Shell](https://en.wikipedia.org/wiki/Shell_(computing))**: on Linux run [`./start.sh`](start.sh) and stop with [`./stop.sh`](stop.sh); on Windows run [`start.bat`](start.bat) and stop with `Ctrl+C`.

### 3. Install and upgrade
- **Linux**: use [`./install.sh`](install.sh) with options like `--service NAME`, `--public` or `--internal`, `--port PORT`, `--upgrade`, `--auto-upgrade`, `--latest`, `--celery`, `--lcd-screen`, `--no-lcd-screen`, `--clean`, `--datasette`. Upgrade with [`./upgrade.sh`](upgrade.sh) using flags such as `--latest`, `--clean`, or `--no-restart`.
- **Windows**: run [`install.bat`](install.bat) to install and [`upgrade.bat`](upgrade.bat) to upgrade.

### 4. Administration
Visit [`http://localhost:8888/admin/`](http://localhost:8888/admin/) for the [Django admin](https://docs.djangoproject.com/en/stable/ref/contrib/admin/) and [`http://localhost:8888/admindocs/`](http://localhost:8888/admindocs/) for the [admindocs](https://docs.djangoproject.com/en/stable/ref/contrib/admin/admindocs/). Use port `8000` if you started with [`start.bat`](start.bat) or the `--public` option.

## Support

Contact us at [tecnologia@gelectriic.com](mailto:tecnologia@gelectriic.com) or visit our [web page](https://www.gelectriic.com/) for [professional services](https://en.wikipedia.org/wiki/Professional_services) and [commercial support](https://en.wikipedia.org/wiki/Technical_support).

## About Me

> "What, you want to know about me too? Well, I enjoy [developing software](https://en.wikipedia.org/wiki/Software_development), [role-playing games](https://en.wikipedia.org/wiki/Role-playing_game), long walks on the [beach](https://en.wikipedia.org/wiki/Beach) and a fourth secret thing."
> --Arthexis

