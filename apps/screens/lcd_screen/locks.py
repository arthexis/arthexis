"""Lock-file IO and payload sources for the LCD screen service."""

from __future__ import annotations

import json
import logging
import os
import socket
import sys
import time
from datetime import datetime, timedelta, timezone as datetime_timezone
from glob import glob
from pathlib import Path
from typing import NamedTuple

import psutil

from apps.core import uptime_utils
from apps.screens.startup_notifications import (
    LCD_CHANNELS_LOCK_FILE,
    LCD_CLOCK_LOCK_FILE,
    LCD_HIGH_LOCK_FILE,
    LCD_LOW_LOCK_FILE,
    LCD_STATS_LOCK_FILE,
    LCD_UPTIME_LOCK_FILE,
    read_lcd_lock_file,
)

from .event_utils import parse_event_expiry, parse_event_expiry_candidate
from .logging import BASE_DIR

logger = logging.getLogger(__name__)

LOCK_DIR = BASE_DIR / ".locks"
HIGH_LOCK_FILE = LOCK_DIR / LCD_HIGH_LOCK_FILE
LOW_LOCK_FILE = LOCK_DIR / LCD_LOW_LOCK_FILE
CLOCK_LOCK_NAME = LCD_CLOCK_LOCK_FILE
UPTIME_LOCK_NAME = LCD_UPTIME_LOCK_FILE
CHANNEL_ORDER_LOCK_NAME = LCD_CHANNELS_LOCK_FILE
DEFAULT_SCROLL_MS = 0
SUITE_UPTIME_LOCK_NAME = "suite_uptime.lck"
SUITE_UPTIME_LOCK_MAX_AGE = timedelta(minutes=10)
INSTALL_DATE_LOCK_NAME = "install_date.lck"
EVENT_LOCK_GLOB = "lcd-event-*.lck"
EVENT_LOCK_PREFIX = "lcd-event-"
EVENT_DEFAULT_DURATION_SECONDS = 3600
SUITE_PORT_DEFAULT = "8888"
SUITE_REACHABILITY_CACHE_SECONDS = 2.0
SUITE_REACHABILITY_TIMEOUT_SECONDS = 0.25
SIMULATOR_STATE_FILE = BASE_DIR / "apps" / "simulators" / "simulator.json"


class LockPayload(NamedTuple):
    line1: str
    line2: str
    scroll_ms: int
    expires_at: datetime | None = None
    is_base: bool = False


class EventPayload(NamedTuple):
    lines: list[str]
    scroll_ms: int


CHANNEL_BASE_NAMES = {
    "high": LCD_HIGH_LOCK_FILE,
    "low": LCD_LOW_LOCK_FILE,
    "clock": CLOCK_LOCK_NAME,
    "uptime": UPTIME_LOCK_NAME,
    "stats": LCD_STATS_LOCK_FILE,
}


_SUITE_REACHABILITY_CACHE = {"checked_at": 0.0, "is_up": False}
_SUITE_AVAILABILITY_STATE = {"is_up": False, "duration_seconds": None, "locked": False}
_SIMULATOR_RUNNING_CACHE = {"checked_at": 0.0, "is_running": False}


def _package_override(name: str, default):
    package = sys.modules.get("apps.screens.lcd_screen")
    if package is None:
        return default
    return getattr(package, name, default)


def _channel_lock_entries(lock_dir: Path, base_name: str) -> list[tuple[int, Path, float]]:
    entries: list[tuple[int, Path, float]] = []
    if not lock_dir.exists():
        return entries
    prefix = f"{base_name}-"
    for path in lock_dir.iterdir():
        name = path.name
        if name == base_name:
            num = 0
        elif name.startswith(prefix):
            suffix = name[len(prefix) :]
            if not suffix.isdigit():
                continue
            num = int(suffix)
        else:
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0.0
        entries.append((num, path, mtime))
    entries.sort(key=lambda item: item[0])
    return entries


def _simulator_running(
    *,
    state_file: Path | None = None,
    cache_seconds: float = 2.0,
) -> bool | None:
    if state_file is None:
        state_file = _package_override("SIMULATOR_STATE_FILE", SIMULATOR_STATE_FILE)
    cache = _package_override("_SIMULATOR_RUNNING_CACHE", _SIMULATOR_RUNNING_CACHE)
    now = time.monotonic()
    if now - cache["checked_at"] <= cache_seconds:
        return cache["is_running"]

    is_running = False
    try:
        payload = json.loads(state_file.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            is_running = any(
                isinstance(state, dict) and state.get("running")
                for state in payload.values()
            )
    except FileNotFoundError:
        pass
    except Exception:
        logger.debug("Failed to read simulator state file", exc_info=True)
        is_running = None

    cache["checked_at"] = now
    cache["is_running"] = is_running
    return is_running


def _read_lock_payload(lock_file: Path, *, now: datetime) -> LockPayload | None:
    payload = read_lcd_lock_file(lock_file)
    if payload is None:
        return None
    if payload.expires_at and payload.expires_at <= now:
        try:
            lock_file.unlink()
        except OSError:
            logger.debug("Failed to remove expired lock file: %s", lock_file, exc_info=True)
        return None
    if payload.expires_at is None and payload.subject.strip().upper().startswith("SIM "):
        simulator_running = _simulator_running()
        if simulator_running is False:
            try:
                lock_file.unlink()
            except OSError:
                logger.debug(
                    "Failed to remove stale simulator lock file: %s",
                    lock_file,
                    exc_info=True,
                )
            return None
    return LockPayload(
        payload.subject,
        payload.body,
        DEFAULT_SCROLL_MS,
        expires_at=payload.expires_at,
    )


def _load_channel_payloads(
    entries: list[tuple[int, Path, float]], *, now: datetime
) -> list[LockPayload]:
    payloads: list[LockPayload] = []
    for _, path, _ in entries:
        payload = _read_lock_payload(path, now=now)
        if payload is not None:
            payloads.append(payload)
    return payloads


def _load_low_channel_payloads(
    entries: list[tuple[int, Path, float]], *, now: datetime
) -> tuple[list[LockPayload], bool]:
    payloads: list[LockPayload] = []
    has_base_payload = False
    for num, path, _ in entries:
        payload = _read_lock_payload(path, now=now)
        if payload is None:
            continue
        if num == 0:
            has_base_payload = True
        payloads.append(payload)
    return payloads, has_base_payload


def _read_lock_file(lock_file: Path) -> LockPayload:
    payload = read_lcd_lock_file(lock_file)
    if payload is None:
        return LockPayload("", "", DEFAULT_SCROLL_MS)
    if payload.expires_at and payload.expires_at <= datetime.now(datetime_timezone.utc):
        try:
            lock_file.unlink()
        except OSError:
            logger.debug("Failed to remove expired lock file: %s", lock_file, exc_info=True)
        return LockPayload("", "", DEFAULT_SCROLL_MS)
    return LockPayload(
        payload.subject,
        payload.body,
        DEFAULT_SCROLL_MS,
        expires_at=payload.expires_at,
    )


def _clear_low_lock_file(
    lock_file: Path = LOW_LOCK_FILE, *, stale_after_seconds: float = 3600
) -> None:
    """Remove stale low-priority lock files without erasing fresh payloads."""

    try:
        stat = lock_file.stat()
    except FileNotFoundError:
        return
    except OSError:
        logger.debug("Unable to stat low LCD lock file", exc_info=True)
        return

    age = time.time() - stat.st_mtime
    if age < stale_after_seconds:
        return

    try:
        contents = lock_file.read_text(encoding="utf-8")
    except OSError:
        logger.debug("Unable to read low LCD lock file", exc_info=True)
        return

    if contents.strip():
        # Preserve populated payloads so uptime messages remain available even
        # when the underlying file is old. The LCD loop refreshes the uptime
        # label on every cycle, so keeping the payload avoids blank screens
        # when the boot-time lock is the only source.
        return

    try:
        lock_file.unlink()
    except FileNotFoundError:
        return
    except OSError:
        logger.debug("Unable to clear low LCD lock file", exc_info=True)


def _event_lock_files(lock_dir: Path = LOCK_DIR) -> list[Path]:
    return sorted(
        (Path(path) for path in glob(str(lock_dir / EVENT_LOCK_GLOB))),
        key=_event_lock_sort_key,
    )


def _event_lock_sort_key(path: Path) -> tuple[int, str]:
    name = path.name
    if name.startswith(EVENT_LOCK_PREFIX) and name.endswith(".lck"):
        suffix = name[len(EVENT_LOCK_PREFIX) : -4]
        if suffix.isdigit():
            return int(suffix), name
    return 10**9, name


def _parse_event_lock_file(lock_file: Path, now: datetime) -> tuple[EventPayload, datetime]:
    try:
        lines = lock_file.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        raise
    except OSError:
        logger.debug("Failed to read event lock file: %s", lock_file, exc_info=True)
        raise

    expires_at: datetime | None = None
    message_lines = lines[:]
    if lines:
        raw = lines[-1].strip()
        if raw:
            expires_at = parse_event_expiry_candidate(raw, now=now)
            if expires_at is not None:
                message_lines = lines[:-1]
    if expires_at is None:
        expires_at = parse_event_expiry(
            None,
            now=now,
            default_seconds=EVENT_DEFAULT_DURATION_SECONDS,
        )
    if not message_lines:
        message_lines = ["", ""]
    normalized_lines = [line[:64] for line in message_lines]
    return EventPayload(normalized_lines, DEFAULT_SCROLL_MS), expires_at


def _parse_channel_order(text: str) -> list[str]:
    channels: list[str] = []
    seen: set[str] = set()
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0]
        if not line.strip():
            continue
        for token in line.replace(",", " ").split():
            normalized = token.strip().lower()
            if not normalized:
                continue
            if normalized in {"full", "all"}:
                normalized = "event"
            if normalized == "uptime":
                normalized = "stats"
            if normalized == "event":
                continue
            if normalized in seen:
                continue
            seen.add(normalized)
            channels.append(normalized)
    return channels


def parse_channel_order(text: str) -> list[str]:
    """Parse channel-order text into normalized channel names."""

    return _parse_channel_order(text)


def _load_channel_order(lock_dir: Path = LOCK_DIR) -> list[str] | None:
    path = lock_dir / CHANNEL_ORDER_LOCK_NAME
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        logger.debug("Unable to read LCD channel order lock file", exc_info=True)
        return None

    requested = _parse_channel_order(raw)
    if not requested:
        return None

    order: list[str] = []
    for name in requested:
        if name not in CHANNEL_BASE_NAMES:
            logger.debug("Skipping unknown LCD channel '%s' in channel order lock", name)
            continue
        order.append(name)
    return order or None


def _parse_start_timestamp(raw: object) -> datetime | None:
    if not raw:
        return None

    text = str(raw).strip()
    if not text:
        return None

    if text[-1] in {"Z", "z"}:
        text = f"{text[:-1]}+00:00"

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime_timezone.utc)

    return parsed.astimezone(datetime_timezone.utc)


def _suite_port(base_dir: Path = BASE_DIR) -> str:
    port_value = (os.getenv("PORT") or "").strip() or SUITE_PORT_DEFAULT
    lock_path = Path(base_dir) / ".locks" / uptime_utils.STARTUP_DURATION_LOCK_NAME
    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = None

    if isinstance(payload, dict):
        lock_port = str(payload.get("port") or "").strip()
        if lock_port:
            return lock_port

    return port_value


def _suite_reachable(
    base_dir: Path = BASE_DIR,
    *,
    timeout: float = SUITE_REACHABILITY_TIMEOUT_SECONDS,
) -> bool:
    try:
        port = int(_suite_port(base_dir))
    except (TypeError, ValueError):
        return False

    if port <= 0:
        return False

    now_value = time.monotonic()
    last_checked = _SUITE_REACHABILITY_CACHE["checked_at"]
    if now_value - last_checked < SUITE_REACHABILITY_CACHE_SECONDS:
        return bool(_SUITE_REACHABILITY_CACHE["is_up"])

    is_up = False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        try:
            sock.connect(("127.0.0.1", port))
            is_up = True
        except OSError:
            pass

    _SUITE_REACHABILITY_CACHE["checked_at"] = now_value
    _SUITE_REACHABILITY_CACHE["is_up"] = is_up
    return is_up


def _boot_elapsed_seconds(
    *, now: datetime | None = None
) -> int | None:
    now_value = now or datetime.now(datetime_timezone.utc)
    try:
        boot_time = float(psutil.boot_time())
    except Exception:
        return None

    if not boot_time:
        return None

    boot_dt = datetime.fromtimestamp(boot_time, tz=datetime_timezone.utc)
    seconds = int((now_value - boot_dt).total_seconds())
    return seconds if seconds >= 0 else None


def _on_seconds(base_dir: Path = BASE_DIR, *, now: datetime | None = None) -> int | None:
    now_value = now or datetime.now(datetime_timezone.utc)
    suite_reachable = _package_override("_suite_reachable", _suite_reachable)
    boot_elapsed = _package_override("_boot_elapsed_seconds", _boot_elapsed_seconds)
    availability_seconds = _package_override("_availability_seconds", _availability_seconds)

    is_up = suite_reachable(base_dir)
    elapsed_seconds = boot_elapsed(now=now_value)

    if is_up:
        available_seconds = availability_seconds(base_dir, now=now_value)
        if available_seconds is not None:
            _SUITE_AVAILABILITY_STATE["is_up"] = True
            _SUITE_AVAILABILITY_STATE["duration_seconds"] = available_seconds
            _SUITE_AVAILABILITY_STATE["locked"] = True
            return available_seconds

        if _SUITE_AVAILABILITY_STATE["is_up"] and _SUITE_AVAILABILITY_STATE["locked"]:
            cached_seconds = _SUITE_AVAILABILITY_STATE["duration_seconds"]
            if cached_seconds is not None:
                return cached_seconds

        _SUITE_AVAILABILITY_STATE["is_up"] = True
        _SUITE_AVAILABILITY_STATE["duration_seconds"] = elapsed_seconds
        _SUITE_AVAILABILITY_STATE["locked"] = False
        return elapsed_seconds

    _SUITE_AVAILABILITY_STATE["is_up"] = False
    _SUITE_AVAILABILITY_STATE["duration_seconds"] = None
    _SUITE_AVAILABILITY_STATE["locked"] = False
    return elapsed_seconds


def _uptime_seconds(
    base_dir: Path = BASE_DIR, *, now: datetime | None = None
) -> int | None:
    lock_path = Path(base_dir) / ".locks" / SUITE_UPTIME_LOCK_NAME
    now_value = now or datetime.now(datetime_timezone.utc)

    payload = None
    lock_fresh = False
    try:
        stats = lock_path.stat()
        heartbeat = datetime.fromtimestamp(stats.st_mtime, tz=datetime_timezone.utc)
        if heartbeat <= now_value:
            lock_fresh = (now_value - heartbeat) <= SUITE_UPTIME_LOCK_MAX_AGE
    except OSError:
        lock_fresh = False

    if lock_fresh:
        try:
            payload = json.loads(lock_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = None

        if isinstance(payload, dict):
            started_at = _parse_start_timestamp(
                payload.get("started_at") or payload.get("boot_time")
            )
            if started_at:
                seconds = int((now_value - started_at).total_seconds())
                if seconds >= 0:
                    return seconds

    try:
        boot_time = float(psutil.boot_time())
    except Exception:
        return None

    if not boot_time:
        return None

    boot_dt = datetime.fromtimestamp(boot_time, tz=datetime_timezone.utc)
    seconds = int((now_value - boot_dt).total_seconds())
    return seconds if seconds >= 0 else None


def _boot_delay_seconds(
    base_dir: Path = BASE_DIR, *, now: datetime | None = None
) -> int | None:
    return uptime_utils.boot_delay_seconds(
        base_dir,
        _parse_start_timestamp,
        now=now,
    )


def _install_date(
    base_dir: Path = BASE_DIR, *, now: datetime | None = None
) -> datetime | None:
    lock_path = Path(base_dir) / ".locks" / INSTALL_DATE_LOCK_NAME
    now_value = now or datetime.now(datetime_timezone.utc)

    try:
        raw = lock_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        raw = ""
    except OSError:
        logger.debug("Unable to read install date lock file", exc_info=True)
        raw = ""

    parsed = _parse_start_timestamp(raw)
    if parsed:
        return parsed

    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(now_value.isoformat(), encoding="utf-8")
    except OSError:
        logger.debug("Unable to write install date lock file", exc_info=True)
        return None

    return now_value


def _down_seconds(
    uptime_seconds: int | None, base_dir: Path = BASE_DIR, *, now: datetime | None = None
) -> int | None:
    if uptime_seconds is None:
        return None

    now_value = now or datetime.now(datetime_timezone.utc)
    install_date = _install_date(base_dir, now=now_value)
    if install_date is None:
        return None

    elapsed_seconds = int((now_value - install_date).total_seconds())
    if elapsed_seconds < 0:
        return 0

    down_seconds = elapsed_seconds - uptime_seconds
    return down_seconds if down_seconds >= 0 else 0


def _duration_from_lock(base_dir: Path, lock_name: str) -> int | None:
    return uptime_utils.duration_from_lock(base_dir, lock_name)


def _availability_seconds(
    base_dir: Path = BASE_DIR, *, now: datetime | None = None
) -> int | None:
    return uptime_utils.availability_seconds(
        base_dir,
        _parse_start_timestamp,
        now=now,
    )
