from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal, InvalidOperation
from glob import glob
from pathlib import Path

DEFAULT_SYSFS_GLOB = "/sys/bus/w1/devices/28-*/temperature"
DEFAULT_I2C_GLOBS = (
    "/sys/bus/i2c/devices/*/hwmon/hwmon*/temp*_input",
    "/sys/class/hwmon/hwmon*/temp*_input",
    "/sys/bus/i2c/devices/*/iio:device*/in_temp_input",
    "/sys/bus/iio/devices/iio:device*/in_temp_input",
)
MILLI_DEGREES_THRESHOLD = Decimal("1000")


def read_w1_temperature(
    paths: Iterable[str | Path] | None = None,
) -> Decimal | None:
    candidates = list(paths or glob(DEFAULT_SYSFS_GLOB))
    for candidate in candidates:
        path = Path(candidate)
        try:
            raw = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if not raw:
            continue
        try:
            value = Decimal(raw)
        except (InvalidOperation, ValueError):
            continue
        if value.copy_abs() >= MILLI_DEGREES_THRESHOLD:
            value = value / MILLI_DEGREES_THRESHOLD
        return value
    return None


def read_i2c_temperature(
    paths: Iterable[str | Path] | None = None,
) -> Decimal | None:
    candidates = list(paths if paths is not None else _iter_i2c_paths())
    for candidate in candidates:
        path = Path(candidate)
        try:
            raw = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if not raw:
            continue
        try:
            value = Decimal(raw)
        except (InvalidOperation, ValueError):
            continue
        if value.copy_abs() >= MILLI_DEGREES_THRESHOLD:
            value = value / MILLI_DEGREES_THRESHOLD
        return value
    return None


def _iter_i2c_paths() -> list[str]:
    paths: list[str] = []
    for pattern in DEFAULT_I2C_GLOBS:
        paths.extend(glob(pattern))
    return paths


def read_temperature(
    *,
    source: str = "auto",
    w1_paths: Iterable[str | Path] | None = None,
    i2c_paths: Iterable[str | Path] | None = None,
) -> Decimal | None:
    normalized = source.strip().lower()
    if normalized == "i2c":
        return read_i2c_temperature(i2c_paths)
    if normalized == "w1":
        return read_w1_temperature(w1_paths)

    if i2c_paths is not None:
        i2c_reading = read_i2c_temperature(i2c_paths)
        if i2c_reading is not None:
            return i2c_reading
    return read_w1_temperature(w1_paths)


def format_w1_temperature(
    *,
    precision: int = 1,
    unit: str = "C",
    paths: Iterable[str | Path] | None = None,
) -> str | None:
    reading = read_w1_temperature(paths)
    if reading is None:
        return None
    precision = max(precision, 0)
    value = f"{reading:.{precision}f}"
    return f"{value}{unit}".strip()


def format_temperature(
    *,
    source: str = "auto",
    precision: int = 1,
    unit: str = "C",
    w1_paths: Iterable[str | Path] | None = None,
    i2c_paths: Iterable[str | Path] | None = None,
) -> str | None:
    reading = read_temperature(source=source, w1_paths=w1_paths, i2c_paths=i2c_paths)
    if reading is None:
        return None
    precision = max(precision, 0)
    value = f"{reading:.{precision}f}"
    return f"{value}{unit}".strip()


__all__ = [
    "format_temperature",
    "format_w1_temperature",
    "read_i2c_temperature",
    "read_temperature",
    "read_w1_temperature",
]
