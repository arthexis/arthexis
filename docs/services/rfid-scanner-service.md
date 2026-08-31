# RFID scanner service

## What it is
The RFID scanner service runs a lightweight background reader for local hardware on Control nodes. It is intentionally started without a Django management command runtime and communicates scan state through lock/log files.

## What it does
- Reads RFID tags from attached hardware in a background worker.
- Writes non-repeated scans to `.locks/rfid-scan.json` and `logs/rfid-scans.ndjson`.
- Adds `last_presence_at` and presence duration fields to the latest-scan lock file so local consumers can ignore cards that have not been physically observed recently.
- Attempts one automatic deep read when the same card remains on the reader for more than two seconds, then keeps the enriched lock-file payload until a different card is scanned.
- Reads sector-0 card names and command metadata during fast scans and includes decoded command payloads and traits after deep reads.
- Shows held command-card progress on the LCD and executes one allowlisted suite command when the hold timer completes.
- Records every command-card execution attempt in the database, including blocked attempts, preflight state, result summaries, and result digests.
- Initializes unformatted held MIFARE Classic cards when managed data sectors are still zeroed.
- Lets Django ingest those artifacts into RFID Attempts for web/API consumers.
- Exposes health checks (ping) and deep-read toggles for diagnostics.

## Latest-scan lock file
The service writes `.locks/rfid-scan.json` with schema `arthexis.rfid.scan.v1`. A normal fast scan includes the RFID, scan time, first and last physical presence timestamps, and presence duration.

When automatic deep-read succeeds, the lock file keeps the returned `keys`, `dump`, and `deep_read` fields for that same card even if later fast reads only refresh its presence. Scanning a different card replaces the enriched payload.

Automatic deep-read timing can be tuned with:
- `RFID_SERVICE_DEEP_SCAN_HOLD_SECONDS` (default `2.0`)
- `RFID_SERVICE_DEEP_SCAN_TIMEOUT` (default `1.0`)
- `RFID_SERVICE_COMMAND_HOLD_SECONDS` (default `3.0`)
- `RFID_SERVICE_COMMAND_LCD_REFRESH_SECONDS` (default `0.5`)
- `RFID_SERVICE_PRESENCE_GAP_SECONDS` (default: the larger of the deep-scan and command hold thresholds)
- `RFID_SERVICE_AUTO_INITIALIZE_UNKNOWN` (default `0`; set `1` to format held uninitialized cards)

## Command-card execution
Command cards are suite-managed MIFARE Classic cards. Sector 0 block 1 stores
the card name, sector 0 block 2 stores the command-card metadata header, later
data blocks store one allowlisted suite command payload, and the remaining data
blocks store result summaries.

When a command card remains on the reader long enough, the service:

1. Deep-reads the command and previous result.
2. Verifies the previous result digest matches the last result recorded for that
   card in the database.
3. Records an `RFIDCommandExecution` row before dispatch.
4. Writes a `started` result summary to the card before the handler runs.
5. Runs only a registered suite command handler.
6. Writes the final result summary and expected digest after completion.

Unrecognized cards, unknown commands, permission failures, and previous-result
digest mismatches are recorded as blocked execution attempts and are not
dispatched. The scan lock file includes a compact `command_execution` object for
the latest held-card command attempt.

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
- The service may write command-card execution ledger rows and card result
  digests during held-card execution. Ordinary scan artifact ingestion still
  belongs to the Django-side RFID attempt path.
- The Suite Services Report lists the RFID service row even if it is not installed so operators know the expected unit name.

## Troubleshooting
- Use the interactive RFID doctor command to verify the service, lock files, and database scan path:
  ```bash
  .venv/bin/python manage.py rfid doctor --scan
  ```
- Add `--deep-read` to toggle deep read mode or `--no-input` to skip prompts.
