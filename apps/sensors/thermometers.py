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
DEFAULT_SOC_THERMAL_ZONE_GLOB = "/sys/class/thermal/thermal_zone*/temp"
DEFAULT_SOC_HWMON_GLOB = "/sys/class/hwmon/hwmon*/temp*_input"
MILLI_DEGREES_THRESHOLD = Decimal("1000")
SOC_SENSOR_NAMES = {"cpu", "cpu_thermal", "soc", "soc_thermal"}


def _normalize_sensor_name(value: str) -> str:
    return value.strip().lower().replace("-", "_")


def _read_temperature_path(path: Path) -> Decimal | None:
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        value = Decimal(raw)
    except (InvalidOperation, ValueError):
        return None
    if value.copy_abs() >= MILLI_DEGREES_THRESHOLD:
        value = value / MILLI_DEGREES_THRESHOLD
    return value


def read_w1_temperature(
    paths: Iterable[str | Path] | None = None,
) -> Decimal | None:
    candidates = list(paths or glob(DEFAULT_SYSFS_GLOB))
    for candidate in candidates:
        value = _read_temperature_path(Path(candidate))
        if value is None:
            continue
        return value
    return None


def read_i2c_temperature(
    paths: Iterable[str | Path] | None = None,
) -> Decimal | None:
    candidates = list(paths if paths is not None else _iter_i2c_paths())
    for candidate in candidates:
        value = _read_temperature_path(Path(candidate))
        if value is None:
            continue
        return value
    return None


def _iter_i2c_paths() -> list[str]:
    paths: list[str] = []
    for pattern in DEFAULT_I2C_GLOBS:
        paths.extend(glob(pattern))
    return paths


def _read_label(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _is_soc_thermal_zone_path(path: Path) -> bool:
    return (
        _normalize_sensor_name(_read_label(path.with_name("type")))
        in SOC_SENSOR_NAMES
    )


def _hwmon_label_path(path: Path) -> Path | None:
    name = path.name
    suffix = "_input"
    if not name.endswith(suffix):
        return None
    return path.with_name(f"{name[: -len(suffix)]}_label")


def _is_soc_hwmon_path(path: Path) -> bool:
    candidates = [_read_label(path.parent / "name")]
    label_path = _hwmon_label_path(path)
    if label_path is not None:
        candidates.append(_read_label(label_path))
    return any(
        _normalize_sensor_name(candidate) in SOC_SENSOR_NAMES
        for candidate in candidates
    )


def read_soc_temperature(
    *,
    thermal_zone_paths: Iterable[str | Path] | None = None,
    hwmon_paths: Iterable[str | Path] | None = None,
) -> Decimal | None:
    zone_candidates = list(
        thermal_zone_paths
        if thermal_zone_paths is not None
        else sorted(glob(DEFAULT_SOC_THERMAL_ZONE_GLOB))
    )
    for candidate in zone_candidates:
        path = Path(candidate)
        if not _is_soc_thermal_zone_path(path):
            continue
        value = _read_temperature_path(path)
        if value is not None:
            return value

    hwmon_candidates = list(
        hwmon_paths
        if hwmon_paths is not None
        else sorted(glob(DEFAULT_SOC_HWMON_GLOB))
    )
    for candidate in hwmon_candidates:
        path = Path(candidate)
        if not _is_soc_hwmon_path(path):
            continue
        value = _read_temperature_path(path)
        if value is not None:
            return value
    return None


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
    "read_soc_temperature",
    "read_temperature",
    "read_w1_temperature",
]
