from __future__ import annotations

from dataclasses import dataclass
import logging
from datetime import datetime
import re
import shutil
import subprocess
from typing import Callable, Iterable

from django.utils import timezone

logger = logging.getLogger(__name__)

I2C_SCANNER = "i2cdetect"


@dataclass(frozen=True)
class DetectedClockDevice:
    bus: int
    address: str
    description: str
    raw_info: str


Scanner = Callable[[int], str]


def _run_i2cdetect(bus: int) -> str:
    tool_path = shutil.which(I2C_SCANNER)
    if not tool_path:
        raise RuntimeError(f"{I2C_SCANNER} is not available")

    result = subprocess.run(
        [tool_path, "-y", str(bus)],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if result.returncode != 0:
        output = (result.stderr or result.stdout or "i2cdetect failed").strip()
        raise RuntimeError(output)
    return result.stdout


def parse_i2cdetect_addresses(output: str) -> list[int]:
    """Return detected hexadecimal addresses from ``i2cdetect`` output."""

    addresses: set[int] = set()
    hex_pattern = re.compile(r"^[0-9a-fA-F]{2}$")
    for line in output.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        try:
            _, payload = line.split(":", 1)
        except ValueError:
            continue
        for token in payload.split():
            if hex_pattern.match(token):
                addresses.add(int(token, 16))
    return sorted(addresses)


def discover_clock_devices(
    *, bus_numbers: Iterable[int] | None = None, scanner: Scanner | None = None
) -> list[DetectedClockDevice]:
    """Return detected clock devices across the provided I2C ``bus_numbers``."""

    buses = tuple(bus_numbers or (1,))
    scan_bus = scanner or _run_i2cdetect
    devices: list[DetectedClockDevice] = []
    for bus in buses:
        try:
            output = scan_bus(bus)
        except Exception as exc:  # pragma: no cover - defensive; hardware dependent
            logger.warning("I2C scan failed for bus %s: %s", bus, exc)
            continue
        for address in parse_i2cdetect_addresses(output):
            description = "DS3231 RTC" if address == 0x68 else "I2C clock device"
            devices.append(
                DetectedClockDevice(
                    bus=bus,
                    address=f"0x{address:02x}",
                    description=description,
                    raw_info=output.strip(),
                )
            )
    return devices


def has_clock_device(
    *, bus_numbers: Iterable[int] | None = None, scanner: Scanner | None = None
) -> bool:
    """Return ``True`` when a clock device is available."""

    return bool(discover_clock_devices(bus_numbers=bus_numbers, scanner=scanner))


def read_hardware_clock_time() -> datetime | None:
    """Return the current time reported by the hardware clock if available.

    Attempts to read from the ``hwclock`` utility when present, returning an
    aware :class:`~datetime.datetime` instance if parsing succeeds. Errors are
    logged and ``None`` is returned so callers can gracefully fall back to the
    Django time source.
    """

    tool_path = shutil.which("hwclock")
    if not tool_path:
        return None

    try:
        result = subprocess.run(
            [tool_path, "--get", "--utc"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except Exception as exc:  # pragma: no cover - defensive; platform specific
        logger.warning("Failed to read hardware clock: %s", exc)
        return None

    if result.returncode != 0:
        message = (result.stderr or result.stdout or "hwclock failed").strip()
        logger.warning("hwclock returned non-zero status: %s", message)
        return None

    output = (result.stdout or result.stderr or "").strip()
    if not output:
        return None

    parsed: datetime | None = None
    try:
        parsed = datetime.fromisoformat(output)
    except ValueError:
        # Some platforms include extra tokens; try parsing the leading values.
        parts = output.split()
        for length in (3, 2):
            try:
                candidate = " ".join(parts[:length])
                parsed = datetime.fromisoformat(candidate)
                break
            except (ValueError, IndexError):
                continue

    if parsed is None:
        logger.warning("Unable to parse hardware clock output: %s", output)
        return None

    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone=timezone.utc)

    return parsed
