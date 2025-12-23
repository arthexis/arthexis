from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path


W1_THERMOMETER_GLOB = "28-*/temperature"


def read_w1_temperature_label(
    device_root: Path = Path("/sys/bus/w1/devices"),
    *,
    precision: int = 1,
) -> str | None:
    """Return a formatted temperature label from a 1-wire sensor file."""

    try:
        candidates = sorted(device_root.glob(W1_THERMOMETER_GLOB))
    except OSError:
        return None

    for candidate in candidates:
        try:
            raw = candidate.read_text(encoding="utf-8").strip()
        except OSError:
            continue

        if not raw:
            continue

        try:
            reading = Decimal(raw)
        except (InvalidOperation, ValueError):
            continue

        if abs(reading) >= Decimal("1000"):
            reading = reading / Decimal("1000")

        precision = max(precision, 0)
        value = f"{reading:.{precision}f}"
        return f"{value}C"

    return None


__all__ = ["read_w1_temperature_label", "W1_THERMOMETER_GLOB"]
