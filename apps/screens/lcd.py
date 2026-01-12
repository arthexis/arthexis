"""Minimal driver for PCF8574/PCF8574A I2C LCD1602 displays.

The implementation is adapted from the example provided in the
instructions. It is intentionally lightweight and only implements the
operations required for this project: initialisation, clearing the
screen and writing text to a specific position.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol

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

    def write_byte_data(
        self, addr: int, cmd: int, data: int
    ) -> None:  # pragma: no cover - thin wrapper
        if smbus is None:
            raise LCDUnavailableError(SMBUS_HINT)
        bus = smbus.SMBus(self.channel)
        bus.write_byte_data(addr, cmd, data)
        bus.close()


@dataclass
class LCDTimings:
    """Timing configuration for the LCD controller."""

    pulse_enable_delay: float = 0.002
    pulse_disable_delay: float = 0.002
    command_delay: float = 0.005
    data_delay: float = 0.003
    clear_delay: float = 0.005

    lock_file_name = "lcd-timings"
    _lock_fields = {
        "pulse_enable_delay": "LCD_PULSE_ENABLE_DELAY",
        "pulse_disable_delay": "LCD_PULSE_DISABLE_DELAY",
        "command_delay": "LCD_COMMAND_DELAY",
        "data_delay": "LCD_DATA_DELAY",
        "clear_delay": "LCD_CLEAR_DELAY",
    }

    @staticmethod
    def _resolve_base_dir() -> Path:
        env_base = os.getenv("ARTHEXIS_BASE_DIR")
        if env_base:
            return Path(env_base)

        cwd = Path.cwd()
        if (cwd / ".locks").exists():
            return cwd

        return Path(__file__).resolve().parents[2]

    @classmethod
    def from_env(cls) -> "LCDTimings":
        """Load timing overrides from environment variables.

        Values are interpreted as seconds to align with :func:`time.sleep`.
        Unknown or invalid values fall back to the defaults.
        """

        def _load(name: str, default: float) -> float:
            raw = os.getenv(name)
            if not raw:
                return default
            try:
                return float(raw)
            except (TypeError, ValueError):
                return default

        return cls(
            pulse_enable_delay=_load("LCD_PULSE_ENABLE_DELAY", cls.pulse_enable_delay),
            pulse_disable_delay=_load("LCD_PULSE_DISABLE_DELAY", cls.pulse_disable_delay),
            command_delay=_load("LCD_COMMAND_DELAY", cls.command_delay),
            data_delay=_load("LCD_DATA_DELAY", cls.data_delay),
            clear_delay=_load("LCD_CLEAR_DELAY", cls.clear_delay),
        )

    @classmethod
    def from_lock_file(cls, lock_file: Path) -> "LCDTimings" | None:
        if not lock_file.exists():
            return None

        values: dict[str, float] = {}
        try:
            contents = lock_file.read_text(encoding="utf-8")
        except OSError:
            return None

        for line in contents.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" not in stripped:
                continue
            key, raw_value = map(str.strip, stripped.split("=", 1))
            if key not in cls._lock_fields:
                continue
            try:
                value = float(raw_value)
            except (TypeError, ValueError):
                continue
            values[key] = value

        if not values:
            return None

        timings = cls()
        for key, value in values.items():
            setattr(timings, key, value)
        return timings

    @classmethod
    def from_configuration(cls, *, base_dir: Path | None = None) -> "LCDTimings":
        base_dir = base_dir or cls._resolve_base_dir()
        lock_file = base_dir / ".locks" / cls.lock_file_name
        timings = cls.from_lock_file(lock_file)
        if timings is not None:
            return timings
        return cls.from_env()

    def to_lock_file(self) -> str:
        lines = ["# LCD timing calibration values (seconds)"]
        for key in self._lock_fields:
            value = getattr(self, key)
            lines.append(f"{key}={value:.6f}")
        lines.append("")
        return "\n".join(lines)


class LCDController(Protocol):
    """Interface for LCD controllers used by the screen service."""

    columns: int
    rows: int

    def init_lcd(self, addr: int | None = None, bl: int = 1) -> None:
        ...

    def clear(self) -> None:
        ...

    def reset(self) -> None:
        ...

    def write(self, x: int, y: int, s: str) -> None:
        ...

    def write_frame(self, line1: str, line2: str, retries: int = 1) -> None:
        ...

    def i2c_scan(self) -> list[str]:
        ...


def scan_i2c_addresses() -> list[str]:  # pragma: no cover - requires hardware
    """Return a list of detected I2C addresses using ``i2cdetect``."""

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


class AiP31068LCD1602:
    """Driver for Waveshare LCD1602 modules using the AiP31068L controller."""

    columns = 16
    rows = 2
    AIP31068_ADDRESS = 0x3E
    _DISPLAY_CONTROL = 0x04
    _DISPLAY_MODE = 0x02
    _FUNCTION = 0x08

    def __init__(
        self,
        bus: _BusWrapper | None = None,
        *,
        address: int | None = None,
    ) -> None:
        if smbus is None:  # pragma: no cover - hardware dependent
            raise LCDUnavailableError(SMBUS_HINT)
        self.bus = bus or _BusWrapper(1)
        self.address = address or self.AIP31068_ADDRESS

    def _send_command(self, cmd: int) -> None:
        self.bus.write_byte_data(self.address, 0x80, cmd & 0xFF)
        time.sleep(0.002)

    def _send_data(self, data: int) -> None:
        self.bus.write_byte_data(self.address, 0x40, data & 0xFF)
        time.sleep(0.001)

    def init_lcd(self, addr: int | None = None, bl: int = 1) -> None:
        if addr is not None:
            self.address = addr
        time.sleep(0.05)
        self._send_command(0x20 | self._FUNCTION)
        time.sleep(0.005)
        self._send_command(0x20 | self._FUNCTION)
        time.sleep(0.005)
        self._send_command(0x20 | self._FUNCTION)
        self._send_command(0x08)
        time.sleep(0.005)
        self.clear()
        self._send_command(0x04 | self._DISPLAY_MODE)
        time.sleep(0.005)
        self._send_command(0x08 | self._DISPLAY_CONTROL)
        time.sleep(0.005)

    def clear(self) -> None:
        self._send_command(0x01)
        time.sleep(0.005)

    def reset(self) -> None:
        self.init_lcd(addr=self.address, bl=1)

    def write(self, x: int, y: int, s: str) -> None:
        x = max(0, min(self.columns - 1, int(x)))
        y = max(0, min(self.rows - 1, int(y)))
        text = str(s)[: self.columns - x]
        addr = 0x80 + 0x40 * y + x
        self._send_command(addr)
        for ch in text:
            self._send_data(ord(ch))

    def write_frame(self, line1: str, line2: str, retries: int = 1) -> None:
        last_error: Exception | None = None
        for _ in range(retries + 1):
            try:
                self._write_row(0, line1)
                self._write_row(1, line2)
                self._send_command(0x02)
                return
            except Exception as exc:  # pragma: no cover - hardware dependent
                last_error = exc
                time.sleep(0.002)
                try:
                    self.reset()
                except Exception:
                    pass
        if last_error:
            raise last_error

    def _write_row(self, row: int, text: str) -> None:
        padded = str(text)[: self.columns].ljust(self.columns)
        addr = 0x80 + 0x40 * max(0, min(self.rows - 1, int(row)))
        self._send_command(addr)
        for ch in padded:
            self._send_data(ord(ch))

    def i2c_scan(self) -> list[str]:  # pragma: no cover - requires hardware
        return scan_i2c_addresses()


def _normalize_driver_preference(preference: str | None = None) -> str:
    raw = (preference or os.getenv("LCD_DRIVER") or os.getenv("LCD_I2C_DRIVER") or "auto").strip()
    return raw.lower() or "auto"


def _resolve_driver(
    preference: str, *, addresses: Iterable[str] | None = None
) -> str:
    if preference in {"aip31068", "waveshare", "aip"}:
        return "aip31068"
    if preference in {"pcf8574", "pcf8574a", "pcf"}:
        return "pcf8574"
    if preference != "auto":
        return "pcf8574"

    tokens = {addr.lower() for addr in (addresses or [])}
    if {"27", "3f"} & tokens:
        return "pcf8574"
    if "3e" in tokens:
        if tokens == {"3e"}:
            return "pcf8574"
        return "aip31068"
    return "pcf8574"


def create_lcd_controller(
    *,
    preference: str | None = None,
    base_dir: Path | None = None,
    addresses: Iterable[str] | None = None,
) -> LCDController:
    driver_preference = _normalize_driver_preference(preference)
    resolved = _resolve_driver(driver_preference, addresses=addresses)
    if resolved == "aip31068":
        return AiP31068LCD1602()
    return CharLCD1602(base_dir=base_dir)


def prepare_lcd_controller(
    *,
    preference: str | None = None,
    base_dir: Path | None = None,
) -> LCDController:
    driver_preference = _normalize_driver_preference(preference)
    addresses = scan_i2c_addresses()
    resolved = _resolve_driver(driver_preference, addresses=addresses)
    lcd = create_lcd_controller(
        preference=resolved,
        base_dir=base_dir,
        addresses=addresses,
    )
    try:
        lcd.init_lcd()
    except Exception:
        if driver_preference == "auto":
            fallback = "pcf8574" if resolved == "aip31068" else "aip31068"
            lcd = create_lcd_controller(
                preference=fallback,
                base_dir=base_dir,
                addresses=addresses,
            )
            lcd.init_lcd()
            return lcd
        raise
    return lcd


class CharLCD1602:
    """Minimal driver for PCF8574/PCF8574A I2C backpack (LCD1602)."""

    columns = 16
    rows = 2
    # Common backpack addresses:
    # - PCF8574:     0x27
    # - PCF8574A:    often 0x3F, but some boards show up as 0x3E
    PCF8574_ADDRESS = 0x27
    PCF8574A_ADDRESS = 0x3F
    PCF8574A_ALT_ADDRESS = 0x3E

    def __init__(
        self,
        bus: _BusWrapper | None = None,
        timings: LCDTimings | None = None,
        *,
        base_dir: Path | None = None,
    ) -> None:
        if smbus is None:  # pragma: no cover - hardware dependent
            raise LCDUnavailableError(SMBUS_HINT)
        self.bus = bus or _BusWrapper(1)
        self.BLEN = 1
        self.LCD_ADDR = self.PCF8574_ADDRESS
        self.timings = timings or LCDTimings.from_configuration(base_dir=base_dir)

    def _write_word(self, addr: int, data: int) -> None:
        if self.BLEN:
            data |= 0x08
        else:
            data &= 0xF7
        self.bus.write_byte(addr, data)

    def _pulse_enable(self, data: int) -> None:
        self._write_word(self.LCD_ADDR, data | 0x04)
        time.sleep(self.timings.pulse_enable_delay)
        self._write_word(self.LCD_ADDR, data & ~0x04)
        time.sleep(self.timings.pulse_disable_delay)

    def send_command(self, cmd: int) -> None:
        high = cmd & 0xF0
        low = (cmd << 4) & 0xF0
        self._write_word(self.LCD_ADDR, high)
        self._pulse_enable(high)
        self._write_word(self.LCD_ADDR, low)
        self._pulse_enable(low)
        # Give the LCD time to process the command to avoid garbled output.
        time.sleep(self.timings.command_delay)

    def send_data(self, data: int) -> None:
        high = (data & 0xF0) | 0x01
        low = ((data << 4) & 0xF0) | 0x01
        self._write_word(self.LCD_ADDR, high)
        self._pulse_enable(high)
        self._write_word(self.LCD_ADDR, low)
        self._pulse_enable(low)
        # Allow the LCD controller to catch up between data writes.
        time.sleep(self.timings.data_delay)

    def i2c_scan(self) -> list[str]:  # pragma: no cover - requires hardware
        """Return a list of detected I2C addresses.

        The implementation relies on the external ``i2cdetect`` command.  On
        systems where ``i2c-tools`` is not installed or the command cannot be
        executed (e.g. insufficient permissions), the function returns an empty
        list so callers can fall back to a sensible default address.
        """

        return scan_i2c_addresses()

    def init_lcd(self, addr: int | None = None, bl: int = 1) -> None:
        self.BLEN = 1 if bl else 0
        if addr is None:
            try:
                found = self.i2c_scan()
            except Exception:  # pragma: no cover - i2c detection issues
                found = []
            found_lower = {token.lower() for token in found}
            if "3f" in found_lower:
                self.LCD_ADDR = self.PCF8574A_ADDRESS
            elif "3e" in found_lower:
                self.LCD_ADDR = self.PCF8574A_ALT_ADDRESS
            else:
                # Default to the common PCF8574 address (0x27) when detection
                # fails or returns no recognised addresses. This mirrors the
                # behaviour prior to introducing automatic address detection and
                # prevents the display from remaining uninitialised on systems
                # without ``i2c-tools``.
                self.LCD_ADDR = self.PCF8574_ADDRESS
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
        time.sleep(self.timings.clear_delay)

    def reset(self) -> None:
        """Re-run the initialisation sequence to recover the display."""
        self.init_lcd(addr=self.LCD_ADDR, bl=self.BLEN)

    def set_backlight(
        self, on: bool = True
    ) -> None:  # pragma: no cover - hardware dependent
        self.BLEN = 1 if on else 0
        self._write_word(self.LCD_ADDR, 0x00)

    def write(self, x: int, y: int, s: str) -> None:
        x = max(0, min(self.columns - 1, int(x)))
        y = max(0, min(self.rows - 1, int(y)))
        text = str(s)[: self.columns - x]
        addr = 0x80 + 0x40 * y + x
        self.send_command(addr)
        for ch in text:
            self.send_data(ord(ch))

    def write_frame(self, line1: str, line2: str, retries: int = 1) -> None:
        """Write two rows as a single transaction with retry and cursor reset."""

        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                self._write_row(0, line1)
                self._write_row(1, line2)
                # Return the cursor home to avoid drifting address pointers when
                # the controller misses an enable pulse.
                self.send_command(0x02)
                return
            except Exception as exc:  # pragma: no cover - hardware dependent
                last_error = exc
                time.sleep(self.timings.command_delay)
                try:
                    self.reset()
                except Exception:
                    # Reset failures are not fatal for retry attempts.
                    pass
        if last_error:
            raise last_error

    def _write_row(self, row: int, text: str) -> None:
        padded = str(text)[: self.columns].ljust(self.columns)
        addr = 0x80 + 0x40 * max(0, min(self.rows - 1, int(row)))
        self.send_command(addr)
        for ch in padded:
            self.send_data(ord(ch))
