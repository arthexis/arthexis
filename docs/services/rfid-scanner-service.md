# RFID scanner service

## What it is
The RFID scanner service runs a lightweight background reader for local hardware. It is intentionally started without a Django management command runtime and communicates scan state through lock/log files.

## What it does
- Reads RFID tags from attached hardware in a background worker.
- Writes non-repeated scans to `.locks/rfid-scan.json` and `logs/rfid-scans.ndjson`.
- Lets Django ingest those artifacts into RFID Attempts for web/API consumers.
- Exposes health checks (ping) and deep-read toggles for diagnostics.

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
- Systemd should launch the service with module execution (`python -m apps.cards.rfid_service`), not `manage.py`.
- The service itself should not write directly to the Django database.
- The Suite Services Report lists the RFID service row even if it is not installed so operators know the expected unit name.

## Troubleshooting
- Use the interactive RFID doctor command to verify the service, lock files, and database scan path:
  ```bash
  .venv/bin/python manage.py rfid doctor --scan
  ```
- Add `--deep-read` to toggle deep read mode or `--no-input` to skip prompts.
