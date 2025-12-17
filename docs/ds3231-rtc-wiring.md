# DS3231 RTC wiring guide

This guide shows how to wire a DS3231 real-time clock (RTC) module to a Raspberry Pi and verify that it is detected over I2C.

## Pinout and connections

| RTC pin | Raspberry Pi pin | Name |
| --- | --- | --- |
| VCC | Pin 1 | 3.3 V power |
| GND | Pin 6 (or any ground) | Ground |
| SDA | Pin 3 | I2C Data (GPIO2) |
| SCL | Pin 5 | I2C Clock (GPIO3) |

**Wiring summary**

- RTC VCC → Pi 3.3 V (Pin 1)
- RTC GND → Pi GND (Pin 6 or any ground)
- RTC SDA → Pi SDA (Pin 3)
- RTC SCL → Pi SCL (Pin 5)

> ⚠️ Power the module with **3.3 V**. The Raspberry Pi’s GPIO pins use 3.3 V logic; feeding them 5 V can damage the board.

## Enable and test I2C on the Pi

1. Enable I2C: run `sudo raspi-config`, go to **Interface Options → I2C → Yes**.
2. Install I2C tools:
   ```bash
   sudo apt update
   sudo apt install i2c-tools
   ```
3. Confirm the RTC is detected:
   ```bash
   sudo i2cdetect -y 1
   ```
   A connected DS3231 should appear at address **0x68**.

Once detected, the GPIO RTC node feature can manage the clock devices from the admin via the **Find Clock Devices** action.
