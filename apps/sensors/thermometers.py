from __future__ import annotations

from decimal import Decimal, InvalidOperation
from glob import glob
from pathlib import Path
from typing import Iterable


DEFAULT_SYSFS_GLOB = "/sys/bus/w1/devices/28-*/temperature"
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


__all__ = ["format_w1_temperature", "read_w1_temperature"]
