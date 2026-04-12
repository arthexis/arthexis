# LCD screen service

## What it is
The LCD screen service drives a 16x2 I²C display on Control nodes, showing uptime, status messages, and queued notifications.

## What it does
- Runs the `apps.screens.lcd_screen` updater loop.
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
- Control presets enable the LCD lock automatically.
- The Suite Services Report lists the LCD row even when the lock is missing so operators can enable it later.
