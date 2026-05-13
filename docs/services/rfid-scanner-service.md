# RFID scanner service

## What it is
The RFID scanner service runs a lightweight background reader for local hardware. It is intentionally started without a Django management command runtime and communicates scan state through lock/log files.

## What it does
- Reads RFID tags from attached hardware in a background worker.
- Writes non-repeated scans to `.locks/rfid-scan.json` and `logs/rfid-scans.ndjson`.
- Adds `last_presence_at` and presence duration fields to the latest-scan lock file so local consumers can ignore cards that have not been physically observed recently.
- Attempts one automatic deep read when the same card remains on the reader for more than two seconds, then keeps the enriched lock-file payload until a different card is scanned.
- Reads sector-0 LCD labels during fast scans and includes decoded traits after deep reads.
- Initializes unformatted held MIFARE Classic cards when managed data sectors are still zeroed.
- Lets Django ingest those artifacts into RFID Attempts for web/API consumers.
- Exposes health checks (ping) and deep-read toggles for diagnostics.

## Latest-scan lock file
The service writes `.locks/rfid-scan.json` with schema `arthexis.rfid.scan.v1`. A normal fast scan includes the RFID, scan time, first and last physical presence timestamps, and presence duration.

When automatic deep-read succeeds, the lock file keeps the returned `keys`, `dump`, and `deep_read` fields for that same card even if later fast reads only refresh its presence. Scanning a different card replaces the enriched payload.

Automatic deep-read timing can be tuned with:
- `RFID_SERVICE_DEEP_SCAN_HOLD_SECONDS` (default `2.0`)
- `RFID_SERVICE_DEEP_SCAN_TIMEOUT` (default `1.0`)
- `RFID_SERVICE_PRESENCE_GAP_SECONDS` (default: same as the hold threshold)
- `RFID_SERVICE_AUTO_INITIALIZE_UNKNOWN` (default `1`; set `0` to disable formatting held uninitialized cards)

Decoded traits are emitted as a `traits` object and `trait_sigils` object. Local
mode runners can pass those values to transition scripts as `SIGIL_*`
environment variables.

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
