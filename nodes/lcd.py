"""Minimal driver for PCF8574/PCF8574A I2C LCD1602 displays.

The implementation is adapted from the example provided in the
instructions.  It is intentionally lightweight and only implements the
operations required for this project: initialisation, clearing the
screen and writing text to a specific position.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass

try:  # pragma: no cover - hardware dependent
    import smbus  # type: ignore
except Exception:  # pragma: no cover - missing dependency
    try:  # pragma: no cover - hardware dependent
        import smbus2 as smbus  # type: ignore
    except Exception:  # pragma: no cover - missing dependency
        smbus = None  # type: ignore

SMBUS_HINT = (
    "smbus module not found. Enable the I2C interface and install the dependencies.\n"
    "For Debian/Ubuntu run: sudo apt-get install i2c-tools python3-smbus\n"
    "Within the virtualenv: pip install smbus2"
)


class LCDUnavailableError(RuntimeError):
    """Raised when the LCD cannot be initialised."""


@dataclass
class _BusWrapper:
    """Wrapper around :class:`smbus.SMBus` to allow mocking in tests."""

    channel: int

    def write_byte(
        self, addr: int, data: int
    ) -> None:  # pragma: no cover - thin wrapper
        if smbus is None:
            raise LCDUnavailableError(SMBUS_HINT)
        bus = smbus.SMBus(self.channel)
        bus.write_byte(addr, data)
        bus.close()


class CharLCD1602:
    """Minimal driver for PCF8574/PCF8574A I2C backpack (LCD1602)."""

    def __init__(self, bus: _BusWrapper | None = None) -> None:
        if smbus is None:  # pragma: no cover - hardware dependent
            raise LCDUnavailableError(SMBUS_HINT)
        self.bus = bus or _BusWrapper(1)
        self.BLEN = 1
        self.PCF8574_address = 0x27
        self.PCF8574A_address = 0x3F
        self.LCD_ADDR = self.PCF8574_address

    def _write_word(self, addr: int, data: int) -> None:
        if self.BLEN:
            data |= 0x08
        else:
            data &= 0xF7
        self.bus.write_byte(addr, data)

    def _pulse_enable(self, data: int) -> None:
        self._write_word(self.LCD_ADDR, data | 0x04)
        time.sleep(0.0005)
        self._write_word(self.LCD_ADDR, data & ~0x04)
        time.sleep(0.0001)

    def send_command(self, cmd: int) -> None:
        high = cmd & 0xF0
        low = (cmd << 4) & 0xF0
        self._write_word(self.LCD_ADDR, high)
        self._pulse_enable(high)
        self._write_word(self.LCD_ADDR, low)
        self._pulse_enable(low)
        # Give the LCD time to process the command to avoid garbled output.
        time.sleep(0.001)

    def send_data(self, data: int) -> None:
        high = (data & 0xF0) | 0x01
        low = ((data << 4) & 0xF0) | 0x01
        self._write_word(self.LCD_ADDR, high)
        self._pulse_enable(high)
        self._write_word(self.LCD_ADDR, low)
        self._pulse_enable(low)
        # Allow the LCD controller to catch up between data writes.
        time.sleep(0.001)

    def i2c_scan(self) -> list[str]:  # pragma: no cover - requires hardware
        """Return a list of detected I2C addresses.

        The implementation relies on the external ``i2cdetect`` command.  On
        systems where ``i2c-tools`` is not installed or the command cannot be
        executed (e.g. insufficient permissions), the function returns an empty
        list so callers can fall back to a sensible default address.
        """

        try:
            output = subprocess.check_output(["i2cdetect", "-y", "1"], text=True)
        except Exception:  # pragma: no cover - depends on environment
            return []

        addresses: list[str] = []
        for line in output.splitlines()[1:]:
            parts = line.split()
            for token in parts[1:]:
                if token != "--":
                    addresses.append(token)
        return addresses

    def init_lcd(self, addr: int | None = None, bl: int = 1) -> None:
        self.BLEN = 1 if bl else 0
        if addr is None:
            try:
                found = self.i2c_scan()
            except Exception:  # pragma: no cover - i2c detection issues
                found = []
            if "3f" in found or "3F" in found:
                self.LCD_ADDR = self.PCF8574A_address
            else:
                # Default to the common PCF8574 address (0x27) when detection
                # fails or returns no recognised addresses. This mirrors the
                # behaviour prior to introducing automatic address detection and
                # prevents the display from remaining uninitialised on systems
                # without ``i2c-tools``.
                self.LCD_ADDR = self.PCF8574_address
        else:
            self.LCD_ADDR = addr

        time.sleep(0.05)
        self.send_command(0x33)
        self.send_command(0x32)
        self.send_command(0x28)
        self.send_command(0x0C)
        self.send_command(0x06)
        self.clear()
        self._write_word(self.LCD_ADDR, 0x00)

    def clear(self) -> None:
        self.send_command(0x01)
        time.sleep(0.002)

    def reset(self) -> None:
        """Re-run the initialisation sequence to recover the display."""
        self.init_lcd(addr=self.LCD_ADDR, bl=self.BLEN)

    def set_backlight(
        self, on: bool = True
    ) -> None:  # pragma: no cover - hardware dependent
        self.BLEN = 1 if on else 0
        self._write_word(self.LCD_ADDR, 0x00)

    def write(self, x: int, y: int, s: str) -> None:
        x = max(0, min(15, int(x)))
        y = 0 if int(y) <= 0 else 1
        addr = 0x80 + 0x40 * y + x
        self.send_command(addr)
        for ch in str(s):
            self.send_data(ord(ch))
