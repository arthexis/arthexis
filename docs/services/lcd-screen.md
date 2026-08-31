# LCD screen service

## What it is
The LCD screen service drives a 16x2 I²C display on Control nodes, showing uptime, status messages, and queued notifications.

## What it does
- Runs the `apps.screens.lcd_screen` updater loop via `python -m apps.screens.lcd_screen.runner`.
- Reads LCD lock files for messages and cycles the display.

## Enable
1. Create the LCD feature lock (usually via the installer):
   ```bash
   touch .locks/lcd_screen.lck
   ```
2. Install and enable the systemd unit when using systemd-managed services:
   ```bash
   sudo systemctl enable --now lcd-<service-name>.service
   ```
3. Ensure I²C hardware support is available (see [LCD Screen Hardware](../lcd-screen-hardware.md)).

## Disable
1. Stop and disable the unit:
   ```bash
   sudo systemctl disable --now lcd-<service-name>.service
   ```
2. Remove the lock file to disable LCD support:
   ```bash
   rm -f .locks/lcd_screen.lck
   ```

## Notes
- The LCD updater is intentionally lock-file driven and does not require direct Django database access.
- Control presets enable the LCD lock automatically.
- The Suite Services Report lists the LCD row even when the lock is missing so operators can enable it later.
- Temperature readings shown from sensor history include a compact source prefix:
  `amb` for ambient thermometers and `soc` for board SoC/CPU temperature.

## Important Notifications
- Use `python manage.py lcd write --subject "DONE" --body "MOVE CARD" --important` for operator-facing messages that must not disappear after one display pass.
- Important notifications use a singleton repeater: each display window keeps `.locks/lcd-high` active for at least 60 seconds, then repeats every 3 minutes until replaced by a newer important message or stopped.
- `lcd write` timing is configurable with `--display-seconds` (default 60), `--repeat-seconds` (default 180), and `--refresh-seconds` (default 15).
- Stop the current singleton repeater with `python manage.py lcd write --stop-important`.
- Runtime state lives in `.locks/lcd-important-repeater.json`; repeater logs are written to `logs/lcd-important-repeater.log`.
