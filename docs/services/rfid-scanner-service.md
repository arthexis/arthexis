# RFID scanner service

## What it is
The RFID scanner service is a lightweight UDP server that brokers RFID reads for local hardware. It is used by features that need quick RFID scans without blocking the main web process.

## What it does
- Listens on the configured host/port for RFID scan requests.
- Executes scan and deep-read operations via attached RFID hardware.

## Enable
1. Enable the RFID service lock (installer or configurator):
   ```bash
   touch .locks/rfid-service.lck
   ```
2. Install and start the systemd unit:
   ```bash
   sudo systemctl enable --now rfid-<service-name>.service
   ```
3. The installer supports `--rfid-service` to set this lock and provision the unit in one step.

## Disable
1. Stop and disable the unit:
   ```bash
   sudo systemctl disable --now rfid-<service-name>.service
   ```
2. Remove the lock file:
   ```bash
   rm -f .locks/rfid-service.lck
   ```
3. You can also run the configurator with `--no-rfid-service` to remove the lock and unit.

## Notes
- The Suite Services Report lists the RFID service row even if it is not installed so operators know the expected unit name.
