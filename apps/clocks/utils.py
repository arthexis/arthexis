from __future__ import annotations

from dataclasses import dataclass
import logging
import re
import shutil
import subprocess
from typing import Callable, Iterable

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
