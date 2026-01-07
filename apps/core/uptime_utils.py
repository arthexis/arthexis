from __future__ import annotations

from datetime import datetime, timezone as datetime_timezone
from pathlib import Path
import json
import re
import shutil
import subprocess
from typing import Callable

import psutil

from apps.core.system import SUITE_UPTIME_LOCK_NAME

INTERNET_ROUTE_TARGET = "8.8.8.8"
STARTUP_DURATION_LOCK_NAME = "startup_duration.lck"
UPGRADE_DURATION_LOCK_NAME = "upgrade_duration.lck"


def ap_mode_enabled(*, timeout: int | float = 5) -> bool:
    nmcli_path = shutil.which("nmcli")
    if not nmcli_path:
        return False

    try:
        result = subprocess.run(
            [
                nmcli_path,
                "-t",
                "-f",
                "TYPE,802-11-wireless.mode",
                "connection",
                "show",
                "--active",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except Exception:
        return False

    if result.returncode != 0:
        return False

    for line in result.stdout.splitlines():
        if not line:
            continue
        parts = line.strip().split(":", 1)
        if len(parts) != 2:
            continue
        conn_type, mode = parts
        if conn_type.lower() in {"wifi", "802-11-wireless"} and mode.strip() == "ap":
            return True

    return False


def internet_interface_label(
    *, route_target: str = INTERNET_ROUTE_TARGET, timeout: int | float = 2
) -> str:
    ip_path = shutil.which("ip")
    if ip_path:
        try:
            result = subprocess.run(
                [ip_path, "route", "get", route_target],
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
            )
        except Exception:
            result = None
        if result and result.returncode == 0:
            match = re.search(r"\bdev\s+(\S+)", result.stdout)
            if match:
                return match.group(1)

    try:
        stats = psutil.net_if_stats()
    except Exception:
        return "NA"

    prioritized_interfaces = ("eth0", "wlan1", "wlan0")
    for name in prioritized_interfaces:
        details = stats.get(name)
        if details and details.isup:
            return name

    for name, details in stats.items():
        if name in prioritized_interfaces or name.startswith("lo"):
            continue
        if details and details.isup:
            return name

    return "NA"


def duration_from_lock(base_dir: Path, lock_name: str) -> int | None:
    lock_path = Path(base_dir) / ".locks" / lock_name
    try:
        raw = lock_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None
    payload = None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict):
        duration_value = payload.get("duration_seconds")
        try:
            duration_seconds = int(float(duration_value))
        except (TypeError, ValueError):
            duration_seconds = None
    else:
        try:
            duration_seconds = int(float(raw.splitlines()[0]))
        except (TypeError, ValueError, IndexError):
            duration_seconds = None
    if duration_seconds is None or duration_seconds < 0:
        return None
    return duration_seconds


def boot_delay_seconds(
    base_dir: Path,
    parse_start_timestamp: Callable[[object], datetime | None],
    *,
    now: datetime | None = None,
) -> int | None:
    lock_path = Path(base_dir) / ".locks" / SUITE_UPTIME_LOCK_NAME
    now_value = now or datetime.now(datetime_timezone.utc)

    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    started_at = parse_start_timestamp(payload.get("started_at") or payload.get("boot_time"))
    if not started_at:
        return None

    try:
        boot_timestamp = float(psutil.boot_time())
    except Exception:
        return None

    boot_time = datetime.fromtimestamp(boot_timestamp, tz=datetime_timezone.utc)
    try:
        boot_time = boot_time.astimezone(started_at.tzinfo or datetime_timezone.utc)
    except Exception:
        return None

    delta_seconds = int((started_at - boot_time).total_seconds())
    if delta_seconds < 0:
        return None
    if now is not None and started_at > now_value:
        return None
    return delta_seconds


def availability_seconds(
    base_dir: Path,
    parse_start_timestamp: Callable[[object], datetime | None],
    *,
    now: datetime | None = None,
) -> int | None:
    candidates = [
        duration_from_lock(base_dir, STARTUP_DURATION_LOCK_NAME),
        duration_from_lock(base_dir, UPGRADE_DURATION_LOCK_NAME),
        boot_delay_seconds(base_dir, parse_start_timestamp, now=now),
    ]
    valid = [value for value in candidates if value is not None]
    return max(valid) if valid else None
