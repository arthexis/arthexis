# LCD screen hardware and driver selection

Arthexis supports 16x2 LCD panels connected over I²C, including the common
PCF8574/PCF8574A backpacks and Waveshare modules based on the AiP31068L
controller. The LCD service chooses the correct driver at startup, and you can
force a specific driver or timing calibration when needed.

All LCD utility commands now run through the unified entrypoint:

```bash
python manage.py lcd <action>
```

Supported actions include `debug`, `replay`, `write`, `animate`, `plan`, and
`calibrate`.

## Supported controllers

| Controller | Typical I²C address | Notes |
| --- | --- | --- |
| PCF8574 / PCF8574A | `0x27`, `0x3F` (sometimes `0x3E`) | Common LCD1602 backpacks. |
| AiP31068L | `0x3E` | Waveshare LCD1602 modules. |

## Driver selection rules

By default the LCD service scans the I²C bus and selects a driver automatically.
The logic is evaluated in the following order:

1. If the bus reports `0x27` or `0x3F`, use the PCF8574 driver.
2. If the bus reports `0x3E` *and* other addresses (but not `0x27` or `0x3F`),
   prefer the AiP31068 driver.
3. If the bus reports only `0x3E`, default to the PCF8574 driver (many backpacks
   report `0x3E` in that configuration).
4. If no recognized addresses are found, fall back to the PCF8574 driver.

To override automatic detection, set `LCD_DRIVER` (or `LCD_I2C_DRIVER`) to one of
`pcf8574` or `aip31068` before starting the service.

## Timing calibration lock file

If a panel still flickers or shows garbled frames, the LCD calibrate command can
save timing overrides to `.locks/lcd-timings`. These values adjust the enable
pulse and command delays for PCF8574-based controllers.

```bash
python manage.py lcd calibrate
```

The resulting lock file is read on startup by the LCD driver so the service
keeps using your tuned settings.

## Quick health check

You can validate the LCD wiring and service state from the command line:

```bash
python manage.py check_lcd_service
```

The command reports lock-file status, checks the systemd unit (when available),
and optionally prompts you to confirm the test message displayed on the panel.
