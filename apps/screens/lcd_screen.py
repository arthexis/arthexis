"""Standalone LCD screen updater with predictable rotations.

The script polls ``.locks/lcd-high`` and ``.locks/lcd-low`` (plus numbered
variants like ``lcd-low-1``) for payload text and writes it to the attached
LCD1602 display. The screen rotates every 10 seconds across three states in
a fixed order: High, Low, and Time/Temp. ``clock`` and ``uptime`` lock files
override the automatic time/uptime payloads, and ``lcd-channels.lck`` can
reorder the channel rotation. By default, rows are truncated to the 16x2
display; scrolling is only enabled when a payload specifies a positive
scroll interval.
"""

from __future__ import annotations

import json
import logging
import math
import os
import random
import signal
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as datetime_timezone
from decimal import Decimal, InvalidOperation
from glob import glob
from pathlib import Path
from typing import Callable, NamedTuple

from itertools import cycle, islice

import psutil


def _resolve_base_dir() -> Path:
    env_base = os.getenv("ARTHEXIS_BASE_DIR")
    if env_base:
        return Path(env_base)

    cwd = Path.cwd()
    if (cwd / ".locks").exists():
        return cwd

    return Path(__file__).resolve().parents[2]


BASE_DIR = _resolve_base_dir()
LOGS_DIR = BASE_DIR / "logs"
LOG_FILE = LOGS_DIR / "lcd-screen.log"
WORK_DIR = BASE_DIR / "work"
WORK_FILE = WORK_DIR / "lcd-screen.txt"
HISTORY_DIR = WORK_DIR
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

LOGS_DIR.mkdir(parents=True, exist_ok=True)
WORK_DIR.mkdir(parents=True, exist_ok=True)
HISTORY_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.DEBUG,
    format=LOG_FORMAT,
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
)
root_logger = logging.getLogger()
if not any(
    isinstance(handler, logging.FileHandler)
    and Path(getattr(handler, "baseFilename", "")) == LOG_FILE
    for handler in root_logger.handlers
):
    file_handler = logging.FileHandler(LOG_FILE)
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root_logger.addHandler(file_handler)
root_logger.setLevel(logging.DEBUG)

from apps.screens.lcd import (
    CharLCD1602,
    LCDController,
    LCDUnavailableError,
    prepare_lcd_controller,
)
from apps.screens.animations import AnimationLoadError, default_tree_frames
from apps.screens.history import LCDHistoryRecorder
from apps.screens.startup_notifications import (
    LCD_CHANNELS_LOCK_FILE,
    LCD_CLOCK_LOCK_FILE,
    LCD_HIGH_LOCK_FILE,
    LCD_LOW_LOCK_FILE,
    LCD_UPTIME_LOCK_FILE,
    read_lcd_lock_file,
)
from apps.core import uptime_utils

logger = logging.getLogger(__name__)
LOCK_DIR = BASE_DIR / ".locks"
HIGH_LOCK_FILE = LOCK_DIR / LCD_HIGH_LOCK_FILE
LOW_LOCK_FILE = LOCK_DIR / LCD_LOW_LOCK_FILE
CLOCK_LOCK_NAME = LCD_CLOCK_LOCK_FILE
UPTIME_LOCK_NAME = LCD_UPTIME_LOCK_FILE
CHANNEL_ORDER_LOCK_NAME = LCD_CHANNELS_LOCK_FILE
DEFAULT_SCROLL_MS = 0
MIN_SCROLL_MS = 50
SCROLL_PADDING = 3
DEFAULT_FALLBACK_SCROLL_SEC = 0.5
LCD_COLUMNS = CharLCD1602.columns
LCD_ROWS = CharLCD1602.rows
CLOCK_TIME_FORMAT = "%p %I:%M"
CLOCK_DATE_FORMAT = "%Y-%m-%d %a"
ROTATION_SECONDS = 10
GAP_ANIMATION_FRAMES_PER_PAYLOAD = 4
GAP_ANIMATION_SCROLL_MS = 600
SUITE_UPTIME_LOCK_NAME = "suite_uptime.lck"
SUITE_UPTIME_LOCK_MAX_AGE = timedelta(minutes=10)
INSTALL_DATE_LOCK_NAME = "install_date.lck"
EVENT_LOCK_GLOB = "lcd-event-*.lck"
EVENT_LOCK_PREFIX = "lcd-event-"
EVENT_DEFAULT_DURATION_SECONDS = 30

try:
    GAP_ANIMATION_FRAMES = default_tree_frames()
except AnimationLoadError:
    logger.debug("Falling back to blank animation frames", exc_info=True)
    GAP_ANIMATION_FRAMES = [" " * (LCD_COLUMNS * LCD_ROWS)]

GAP_ANIMATION_CYCLE = cycle(GAP_ANIMATION_FRAMES)


class FateDeck:
    """Shuffle and draw from a 55-card fate deck."""

    suits = ("D", "H", "C", "V")
    values = ("A", "2", "3", "4", "5", "6", "7", "8", "9", "X", "J", "Q", "K")
    jokers = ("XX", "XY", "YY")

    def __init__(self, *, rng: random.Random | None = None) -> None:
        self.rng = rng or random.Random()
        self._cards: list[str] = []
        self._reshuffle()

    def _reshuffle(self) -> None:
        deck = [f"{suit}{value}" for suit in self.suits for value in self.values]
        deck.extend(self.jokers)
        self.rng.shuffle(deck)
        self._cards = deck

    def draw(self) -> str:
        if not self._cards:
            self._reshuffle()
        return self._cards.pop()

    @property
    def remaining(self) -> int:
        return len(self._cards)


FATE_VECTOR = ""
_fate_deck = FateDeck()


def _write_work_display(line1: str, line2: str, *, target: Path = WORK_FILE) -> None:
    row1 = line1.ljust(LCD_COLUMNS)[:LCD_COLUMNS]
    row2 = line2.ljust(LCD_COLUMNS)[:LCD_COLUMNS]
    try:
        target.write_text(f"{row1}\n{row2}\n", encoding="utf-8")
    except Exception:
        logger.debug("Failed to write LCD fallback output", exc_info=True)


class LockPayload(NamedTuple):
    line1: str
    line2: str
    scroll_ms: int


class DisplayState(NamedTuple):
    pad1: str
    pad2: str
    steps1: int
    steps2: int
    index1: int
    index2: int
    scroll_sec: float
    cycle: int
    last_segment1: str | None
    last_segment2: str | None


_NON_ASCII_CACHE: set[str] = set()


@dataclass
class ChannelCycle:
    payloads: list[LockPayload]
    signature: tuple[tuple[int, float], ...]
    index: int = 0

    def next_payload(self) -> LockPayload | None:
        if not self.payloads:
            return None
        payload = self.payloads[self.index % len(self.payloads)]
        self.index = (self.index + 1) % len(self.payloads)
        return payload


def _non_ascii_positions(text: str) -> list[str]:
    printable = {9, 10, 13} | set(range(32, 127))
    return [f"0x{ord(ch):02x}@{idx}" for idx, ch in enumerate(text) if ord(ch) not in printable]


def _warn_on_non_ascii_payload(payload: LockPayload, label: str) -> None:
    cache_key = (label, payload.line1, payload.line2)
    if cache_key in _NON_ASCII_CACHE:
        return

    issues = _non_ascii_positions(payload.line1) + _non_ascii_positions(payload.line2)
    if issues:
        logger.warning("Non-ASCII characters detected in %s payload: %s", label, ", ".join(issues))
        _NON_ASCII_CACHE.add(cache_key)


class LCDFrameWriter:
    """Write full LCD frames with retry, batching, and history capture."""

    def __init__(
        self,
        lcd: LCDController | None,
        *,
        work_file: Path = WORK_FILE,
        history_recorder: LCDHistoryRecorder | None = None,
    ) -> None:
        self.lcd = lcd
        self.work_file = work_file
        self.history_recorder = history_recorder

    def write(
        self,
        line1: str,
        line2: str,
        *,
        label: str | None = None,
        timestamp: datetime | None = None,
    ) -> bool:
        row1 = line1.ljust(LCD_COLUMNS)[:LCD_COLUMNS]
        row2 = line2.ljust(LCD_COLUMNS)[:LCD_COLUMNS]

        if self.lcd is None:
            _write_work_display(row1, row2, target=self.work_file)
            self._record_history(row1, row2, label=label, timestamp=timestamp)
            return False

        try:
            self.lcd.write_frame(row1, row2, retries=1)
        except Exception as exc:
            logger.warning(
                "LCD write failed; writing to fallback file: %s", exc, exc_info=True
            )
            _write_work_display(row1, row2, target=self.work_file)
            self.lcd = None
            self._record_history(row1, row2, label=label, timestamp=timestamp)
            return False

        self._record_history(row1, row2, label=label, timestamp=timestamp)
        return True

    def _record_history(
        self,
        row1: str,
        row2: str,
        *,
        label: str | None,
        timestamp: datetime | None,
    ) -> None:
        if not self.history_recorder:
            return

        try:
            self.history_recorder.record(
                row1,
                row2,
                label=label,
                timestamp=timestamp or datetime.now(datetime_timezone.utc),
            )
        except Exception:
            logger.debug("Unable to record LCD history", exc_info=True)


class LCDHealthMonitor:
    """Track LCD failures and compute exponential backoff."""

    def __init__(self, *, base_delay: float = 0.5, max_delay: float = 8.0) -> None:
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.failure_count = 0

    def record_failure(self) -> float:
        self.failure_count += 1
        return min(self.base_delay * (2 ** (self.failure_count - 1)), self.max_delay)

    def record_success(self) -> None:
        self.failure_count = 0


class LCDWatchdog:
    """Request periodic resets to keep the controller healthy."""

    def __init__(self, *, reset_every: int = 300) -> None:
        self.reset_every = reset_every
        self._counter = 0

    def tick(self) -> bool:
        self._counter += 1
        return self._counter >= self.reset_every

    def reset(self) -> None:
        self._counter = 0


class ScrollScheduler:
    """Ensure scroll cadence is driven by time rather than loop duration."""

    def __init__(self) -> None:
        self.next_deadline = time.monotonic()

    def sleep_until_ready(self) -> None:
        now = time.monotonic()
        if now < self.next_deadline:
            time.sleep(self.next_deadline - now)

    def advance(self, interval: float) -> None:
        now = time.monotonic()
        self.next_deadline = max(self.next_deadline + interval, now + interval)


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
    return LockPayload(payload.subject, payload.body, DEFAULT_SCROLL_MS)


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
    return LockPayload(payload.subject, payload.body, DEFAULT_SCROLL_MS)


def _has_visible_text(text: str) -> bool:
    return any(ch.isprintable() and not ch.isspace() for ch in text)


def _payload_has_text(payload: LockPayload) -> bool:
    return _has_visible_text(payload.line1) or _has_visible_text(payload.line2)


def _animation_payload(
    frame_cycle, *, frames_per_payload: int = GAP_ANIMATION_FRAMES_PER_PAYLOAD, scroll_ms: int = GAP_ANIMATION_SCROLL_MS
) -> LockPayload:
    frames = list(islice(frame_cycle, frames_per_payload))
    if not frames:
        return LockPayload("", "", scroll_ms)

    line1 = " ".join(frame[:LCD_COLUMNS] for frame in frames).rstrip()
    line2 = " ".join(frame[LCD_COLUMNS:] for frame in frames).rstrip()
    return LockPayload(line1, line2, scroll_ms)


def _select_low_payload(
    payload: LockPayload,
    frame_cycle=GAP_ANIMATION_CYCLE,
    *,
    base_dir: Path = BASE_DIR,
    now: datetime | None = None,
    scroll_ms: int | None = None,
    frames_per_payload: int | None = None,
) -> LockPayload:
    if _payload_has_text(payload):
        return payload

    now_value = now or datetime.now(datetime_timezone.utc)
    _install_date(base_dir, now=now_value)
    uptime_secs = _uptime_seconds(base_dir, now=now_value)
    uptime_label = _format_uptime_label(uptime_secs) or "?d?h?m"
    on_label = _format_on_label(_availability_seconds(base_dir, now=now_value)) or "?m?s"
    subject_parts = [f"UP {uptime_label}"]
    if _ap_mode_enabled():
        ap_client_count = _ap_client_count()
        if ap_client_count is None:
            subject_parts.append("AP")
        else:
            subject_parts.append(f"AP{ap_client_count}")
    subject = " ".join(subject_parts).strip()
    interface_label = _internet_interface_label()
    body_parts = [f"ON {on_label}"]
    if interface_label:
        body_parts.append(interface_label)
    body = " ".join(body_parts).strip()
    return LockPayload(subject, body, DEFAULT_SCROLL_MS)


def _apply_low_payload_fallback(payload: LockPayload) -> LockPayload:
    return _select_low_payload(
        payload,
        frame_cycle=GAP_ANIMATION_CYCLE,
        base_dir=BASE_DIR,
        scroll_ms=GAP_ANIMATION_SCROLL_MS,
    )


CHANNEL_BASE_NAMES = {
    "high": LCD_HIGH_LOCK_FILE,
    "low": LCD_LOW_LOCK_FILE,
    "clock": CLOCK_LOCK_NAME,
    "uptime": UPTIME_LOCK_NAME,
}


def _parse_channel_order(text: str) -> list[str]:
    channels: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0]
        if not line.strip():
            continue
        for token in line.replace(",", " ").split():
            normalized = token.strip().lower()
            if not normalized:
                continue
            channels.append(normalized)
    return channels


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


def _lcd_clock_enabled() -> bool:
    raw = (os.getenv("DISABLE_LCD_CLOCK") or "").strip().lower()
    return raw not in {"1", "true", "yes", "on"}


def _lcd_temperature_label() -> str | None:
    try:
        label = _lcd_temperature_label_from_sensors()
    except Exception:
        logger.debug("Unable to load thermometer data", exc_info=True)
        label = None
    if label:
        return label
    try:
        return _lcd_temperature_label_from_sysfs()
    except Exception:
        logger.debug("Thermometer sysfs read failed", exc_info=True)
    return None


def _format_temperature_value(value: Decimal, unit: str) -> str:
    precision = ".0f" if value.copy_abs() >= Decimal("100") else ".1f"
    return f"{value:{precision}}{unit.upper()}"


def _parse_temperature_label(label: str) -> tuple[Decimal | None, str]:
    if not label:
        return None, ""

    unit = label[-1]
    try:
        value = Decimal(label[:-1])
    except (InvalidOperation, ValueError):
        return None, unit

    return value, unit


def _use_fate_vector() -> bool:
    return random.random() < 0.5


def _draw_fate_vector(deck: FateDeck | None = None) -> str:
    global FATE_VECTOR
    card = (deck or _fate_deck).draw()
    FATE_VECTOR = card
    return card


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


def _uptime_components(seconds: int | None) -> tuple[int, int, int] | None:
    if seconds is None or seconds < 0:
        return None

    minutes_total, _ = divmod(seconds, 60)
    days, remaining_minutes = divmod(minutes_total, 24 * 60)
    hours, minutes = divmod(remaining_minutes, 60)
    return days, hours, minutes


def _ap_mode_enabled() -> bool:
    return uptime_utils.ap_mode_enabled()


def _ap_client_count() -> int | None:
    return uptime_utils.ap_client_count()


def _internet_interface_label() -> str:
    return uptime_utils.internet_interface_label()


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


def _format_uptime_label(seconds: int | None) -> str | None:
    components = _uptime_components(seconds)
    if components is None:
        return None

    days, hours, minutes = components
    return f"{days}d{hours}h{minutes}m"


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


def _format_on_label(seconds: int | None) -> str | None:
    if seconds is None or seconds < 0:
        return None
    minutes_total, secs = divmod(seconds, 60)
    return f"{minutes_total}m{secs}s"


def _refresh_uptime_payload(
    payload: LockPayload, *, base_dir: Path = BASE_DIR, now: datetime | None = None
) -> LockPayload:
    has_uptime = payload.line1.startswith("UP ")
    if not has_uptime:
        return payload

    uptime_secs = _uptime_seconds(base_dir, now=now)
    uptime_label = _format_uptime_label(uptime_secs)
    if not uptime_label:
        return payload

    suffix = payload.line1[len("UP "):].strip()
    extra_suffix = suffix.split(maxsplit=1)[1].strip() if " " in suffix else ""
    subject = f"UP {uptime_label}"
    if extra_suffix:
        subject = f"{subject} {extra_suffix}"

    return payload._replace(line1=subject)


def _lcd_temperature_label_from_sensors() -> str | None:
    return None


def _lcd_temperature_label_from_sysfs() -> str | None:
    try:
        from apps.sensors.thermometers import format_w1_temperature
    except Exception:
        format_w1_temperature = None

    if format_w1_temperature:
        try:
            label = format_w1_temperature()
        except Exception:
            logger.debug("Unable to load sysfs thermometer reading", exc_info=True)
        else:
            if label:
                value, unit = _parse_temperature_label(label)
                return _format_temperature_value(value, unit) if value else label

    for path in glob("/sys/bus/w1/devices/28-*/temperature"):
        try:
            raw = Path(path).read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if not raw:
            continue
        try:
            value = Decimal(raw)
        except (InvalidOperation, ValueError):
            continue
        if value.copy_abs() >= Decimal("1000"):
            value = value / Decimal("1000")
        return _format_temperature_value(value, "C")
    return None


def _clock_payload(
    now: datetime,
    *,
    use_fahrenheit: bool = False,
    fate_deck: FateDeck | None = None,
    choose_fate: Callable[[], bool] | None = None,
) -> tuple[str, str, int, str]:
    temperature = _lcd_temperature_label()
    temp_value, unit = _parse_temperature_label(temperature or "")
    if temp_value is not None:
        if use_fahrenheit and unit.upper() == "C":
            temp_value = temp_value * Decimal("9") / Decimal("5") + Decimal("32")
            temperature = _format_temperature_value(temp_value, "F")
        else:
            temperature = _format_temperature_value(temp_value, unit)
    week_label = f"{now.isocalendar().week:02d}"
    date_label = f"{now.strftime(CLOCK_DATE_FORMAT)}{week_label}"
    fate = choose_fate or _use_fate_vector
    prefix = _draw_fate_vector(fate_deck) if fate() else now.strftime("%p")
    time_label = f"{prefix} {now.strftime('%I:%M')}"
    if temperature:
        time_label = f"{time_label} @ {temperature}"
    return (
        date_label,
        time_label,
        DEFAULT_SCROLL_MS,
        "clock",
    )


_SHUTDOWN_REQUESTED = False
_EVENT_INTERRUPT_REQUESTED = False


def _request_shutdown(signum, frame) -> None:  # pragma: no cover - signal handler
    """Mark the loop for shutdown when the process receives a signal."""

    global _SHUTDOWN_REQUESTED
    _SHUTDOWN_REQUESTED = True


def _shutdown_requested() -> bool:
    return _SHUTDOWN_REQUESTED


def _reset_shutdown_flag() -> None:
    global _SHUTDOWN_REQUESTED
    _SHUTDOWN_REQUESTED = False


def _request_event_interrupt(signum, frame) -> None:  # pragma: no cover - signal handler
    """Interrupt the LCD cycle to show event lock files immediately."""

    global _EVENT_INTERRUPT_REQUESTED
    _EVENT_INTERRUPT_REQUESTED = True


def _event_interrupt_requested() -> bool:
    return _EVENT_INTERRUPT_REQUESTED


def _reset_event_interrupt_flag() -> None:
    global _EVENT_INTERRUPT_REQUESTED
    _EVENT_INTERRUPT_REQUESTED = False


def _blank_display(lcd: LCDController | None) -> None:
    """Clear the LCD and write empty lines to leave a known state."""

    if lcd is None:
        return

    try:
        lcd.clear()
        blank_row = " " * LCD_COLUMNS
        for row in range(LCD_ROWS):
            lcd.write(0, row, blank_row)
    except Exception:
        logger.debug("Failed to blank LCD during shutdown", exc_info=True)


def _handle_shutdown_request(lcd: LCDController | None) -> bool:
    """Blank the display and signal the loop to exit when shutting down."""

    if not _shutdown_requested():
        return False

    _blank_display(lcd)
    return True


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


def _parse_event_lock_file(lock_file: Path, now: datetime) -> tuple[LockPayload, datetime]:
    try:
        lines = lock_file.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        raise
    except OSError:
        logger.debug("Failed to read event lock file: %s", lock_file, exc_info=True)
        raise

    subject = lines[0][:64] if lines else ""
    body = lines[1][:64] if len(lines) > 1 else ""
    expires_at: datetime | None = None
    if len(lines) > 2:
        raw = lines[2].strip()
        if raw:
            if raw.isdigit():
                expires_at = now + timedelta(seconds=int(raw))
            else:
                try:
                    parsed = datetime.fromisoformat(raw)
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=datetime_timezone.utc)
                    expires_at = parsed.astimezone(datetime_timezone.utc)
                except ValueError:
                    expires_at = None
    if expires_at is None:
        expires_at = now + timedelta(seconds=EVENT_DEFAULT_DURATION_SECONDS)
    return LockPayload(subject, body, DEFAULT_SCROLL_MS), expires_at


def _display(
    lcd: LCDController | None, line1: str, line2: str, scroll_ms: int
) -> None:
    state = _prepare_display_state(line1, line2, scroll_ms)
    _advance_display(state, LCDFrameWriter(lcd))


def _prepare_display_state(line1: str, line2: str, scroll_ms: int) -> DisplayState:
    if scroll_ms > 0:
        scroll_sec = max(scroll_ms, MIN_SCROLL_MS) / 1000.0
        text1 = line1[:64]
        text2 = line2[:64]
        pad1 = (
            text1 + " " * SCROLL_PADDING
            if len(text1) > LCD_COLUMNS
            else text1.ljust(LCD_COLUMNS)
        )
        pad2 = (
            text2 + " " * SCROLL_PADDING
            if len(text2) > LCD_COLUMNS
            else text2.ljust(LCD_COLUMNS)
        )
        steps1 = max(len(pad1) - (LCD_COLUMNS - 1), 1)
        steps2 = max(len(pad2) - (LCD_COLUMNS - 1), 1)
    else:
        scroll_sec = DEFAULT_FALLBACK_SCROLL_SEC
        pad1 = line1[:LCD_COLUMNS].ljust(LCD_COLUMNS)
        pad2 = line2[:LCD_COLUMNS].ljust(LCD_COLUMNS)
        steps1 = 1
        steps2 = 1
    cycle = math.lcm(steps1, steps2)
    return DisplayState(
        pad1,
        pad2,
        steps1,
        steps2,
        0,
        0,
        scroll_sec,
        cycle,
        None,
        None,
    )


def _advance_display(
    state: DisplayState,
    frame_writer: LCDFrameWriter,
    *,
    label: str | None = None,
    timestamp: datetime | None = None,
) -> tuple[DisplayState, bool]:
    if _shutdown_requested():
        return state, True

    segment1 = state.pad1[state.index1 : state.index1 + LCD_COLUMNS]
    segment2 = state.pad2[state.index2 : state.index2 + LCD_COLUMNS]

    write_required = segment1 != state.last_segment1 or segment2 != state.last_segment2
    write_success = True
    if write_required:
        write_success = frame_writer.write(
            segment1.ljust(LCD_COLUMNS),
            segment2.ljust(LCD_COLUMNS),
            label=label,
            timestamp=timestamp,
        )

    next_index1 = (state.index1 + 1) % state.steps1
    next_index2 = (state.index2 + 1) % state.steps2
    return (
        state._replace(
            index1=next_index1,
            index2=next_index2,
            last_segment1=segment1,
            last_segment2=segment2,
        ),
        write_success,
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


def _initialize_lcd() -> LCDController:
    return prepare_lcd_controller()


def _load_next_event(
    now_dt: datetime,
) -> tuple[DisplayState | None, datetime | None, Path | None]:
    for candidate in _event_lock_files():
        try:
            payload, expires_at = _parse_event_lock_file(candidate, now_dt)
        except FileNotFoundError:
            continue
        except OSError:
            continue
        if expires_at <= now_dt:
            try:
                candidate.unlink()
            except OSError:
                logger.debug(
                    "Failed to remove expired event lock: %s",
                    candidate,
                    exc_info=True,
                )
            continue
        event_state = _prepare_display_state(
            payload.line1, payload.line2, payload.scroll_ms
        )
        return event_state, expires_at, candidate
    return None, None, None


def main() -> None:  # pragma: no cover - hardware dependent
    lcd = None
    display_state: DisplayState | None = None
    next_display_state: DisplayState | None = None
    event_state: DisplayState | None = None
    event_deadline: datetime | None = None
    event_lock_file: Path | None = None
    rotation_deadline = 0.0
    scroll_scheduler = ScrollScheduler()
    state_order = ("high", "low", "clock")
    state_index = 0
    history_recorder = LCDHistoryRecorder(base_dir=BASE_DIR, history_dir_name="work")
    clock_cycle = 0
    health = LCDHealthMonitor()
    watchdog = LCDWatchdog()
    channel_states: dict[str, ChannelCycle] = {}
    frame_writer: LCDFrameWriter = LCDFrameWriter(None, history_recorder=history_recorder)
    _clear_low_lock_file()

    signal.signal(signal.SIGTERM, _request_shutdown)
    signal.signal(signal.SIGINT, _request_shutdown)
    signal.signal(signal.SIGHUP, _request_shutdown)
    signal.signal(signal.SIGUSR1, _request_event_interrupt)

    def _load_channel_states(
        now_dt: datetime,
    ) -> tuple[dict[str, ChannelCycle], dict[str, bool]]:
        channel_info: dict[str, ChannelCycle] = {}
        channel_text: dict[str, bool] = {}
        for label, base_name in CHANNEL_BASE_NAMES.items():
            entries = _channel_lock_entries(LOCK_DIR, base_name)
            existing = channel_states.get(label)
            signature = tuple((num, mtime) for num, _, mtime in entries)
            payloads: list[LockPayload] = []
            if label == "low":
                payloads, has_base_payload = _load_low_channel_payloads(
                    entries, now=now_dt
                )
                if not has_base_payload:
                    payloads.insert(0, LockPayload("", "", DEFAULT_SCROLL_MS))
                    signature = ((0, -1.0),) + signature
            else:
                payloads = _load_channel_payloads(entries, now=now_dt)
            if (
                existing is None
                or existing.signature != signature
                or payloads != existing.payloads
            ):
                next_index = 0
                if existing and payloads:
                    next_index = existing.index % len(payloads)
                existing = ChannelCycle(
                    payloads=payloads,
                    signature=signature,
                    index=next_index,
                )
            channel_states[label] = existing
            channel_info[label] = existing
            channel_text[label] = any(
                _payload_has_text(payload) for payload in existing.payloads
            )
        return channel_info, channel_text

    def _payload_for_state(
        state_order: tuple[str, ...],
        index: int,
        channel_info: dict[str, ChannelCycle],
        channel_text: dict[str, bool],
        now_dt: datetime,
        *,
        advance: bool = True,
    ) -> LockPayload:
        nonlocal clock_cycle
        state_label = state_order[index]
        channel_state = channel_info.get(state_label)
        if state_label == "high" and channel_state:
            payload = (
                channel_state.next_payload()
                if advance
                else channel_state.payloads[0]
                if channel_state.payloads
                else None
            )
            return payload or LockPayload("", "", DEFAULT_SCROLL_MS)
        if state_label in {"low", "uptime"} and channel_state:
            payload = (
                channel_state.next_payload()
                if advance
                else channel_state.payloads[0]
                if channel_state.payloads
                else None
            )
            if payload and _payload_has_text(payload):
                return _refresh_uptime_payload(payload)
            return _select_low_payload(
                LockPayload("", "", DEFAULT_SCROLL_MS),
                base_dir=BASE_DIR,
                now=now_dt,
            )
        if state_label == "clock":
            if channel_state and channel_text[state_label]:
                payload = (
                    channel_state.next_payload()
                    if advance
                    else channel_state.payloads[0]
                    if channel_state.payloads
                    else None
                )
                return payload or LockPayload("", "", DEFAULT_SCROLL_MS)
            if _lcd_clock_enabled():
                use_fahrenheit = clock_cycle % 2 == 0
                line1, line2, speed, _ = _clock_payload(
                    now_dt.astimezone(), use_fahrenheit=use_fahrenheit
                )
                clock_cycle += 1
                return LockPayload(line1, line2, speed)
        return LockPayload("", "", DEFAULT_SCROLL_MS)

    try:
        try:
            lcd = _initialize_lcd()
            frame_writer = LCDFrameWriter(lcd, history_recorder=history_recorder)
            health.record_success()
        except LCDUnavailableError as exc:
            logger.warning("LCD unavailable during startup: %s", exc)
        except Exception as exc:
            logger.warning("LCD startup failed: %s", exc, exc_info=True)

        while True:
            if _handle_shutdown_request(lcd):
                break

            try:
                now = time.monotonic()
                now_dt = datetime.now(datetime_timezone.utc)

                if _event_interrupt_requested():
                    _reset_event_interrupt_flag()
                    (
                        event_state,
                        event_deadline,
                        event_lock_file,
                    ) = _load_next_event(now_dt)
                elif event_state is None:
                    (
                        pending_state,
                        pending_deadline,
                        pending_lock_file,
                    ) = _load_next_event(now_dt)
                    if pending_state is not None:
                        event_state = pending_state
                        event_deadline = pending_deadline
                        event_lock_file = pending_lock_file

                if event_state is not None and event_deadline is not None:
                    if now_dt >= event_deadline:
                        if event_lock_file:
                            try:
                                event_lock_file.unlink()
                            except OSError:
                                logger.debug(
                                    "Failed to remove event lock file: %s",
                                    event_lock_file,
                                    exc_info=True,
                                )
                        (
                            event_state,
                            event_deadline,
                            event_lock_file,
                        ) = _load_next_event(now_dt)
                        if event_state is not None:
                            continue
                        event_state = None
                        event_deadline = None
                        event_lock_file = None
                        if state_order:
                            state_index = (state_index + 1) % len(state_order)
                        display_state = None
                        next_display_state = None
                        rotation_deadline = 0.0
                        continue

                    if lcd is None:
                        lcd = _initialize_lcd()
                        frame_writer = LCDFrameWriter(
                            lcd, history_recorder=history_recorder
                        )
                        health.record_success()

                    scroll_scheduler.sleep_until_ready()
                    frame_timestamp = datetime.now(datetime_timezone.utc)
                    event_state, write_success = _advance_display(
                        event_state,
                        frame_writer,
                        label="event",
                        timestamp=frame_timestamp,
                    )
                    if write_success:
                        health.record_success()
                        if lcd and watchdog.tick():
                            lcd.reset()
                            watchdog.reset()
                    else:
                        if lcd is not None and frame_writer.lcd is None:
                            lcd = None
                            frame_writer = LCDFrameWriter(
                                None, history_recorder=history_recorder
                            )
                        delay = health.record_failure()
                        time.sleep(delay)
                    scroll_scheduler.advance(
                        (event_state.scroll_sec if event_state else 0)
                        or DEFAULT_FALLBACK_SCROLL_SEC
                    )
                    continue

                if display_state is None or now >= rotation_deadline:
                    channel_info, channel_text = _load_channel_states(now_dt)

                    configured_order = _load_channel_order(LOCK_DIR)

                    def _channel_available(label: str) -> bool:
                        if label == "high":
                            return bool(channel_info[label].signature)
                        if label == "clock":
                            return channel_text[label] or _lcd_clock_enabled()
                        if label in {"low", "uptime"}:
                            return True
                        return False

                    previous_order = state_order
                    if configured_order:
                        state_order = tuple(
                            label
                            for label in configured_order
                            if _channel_available(label)
                        )
                        if not state_order:
                            state_order = ("clock",)
                    else:
                        high_available = _channel_available("high")
                        low_available = _channel_available("low")
                        if high_available:
                            state_order = (
                                ("high", "low", "clock")
                                if low_available
                                else ("high", "clock")
                            )
                        else:
                            state_order = ("low", "clock") if low_available else ("clock",)

                    if previous_order and 0 <= state_index < len(previous_order):
                        current_label = previous_order[state_index]
                        if current_label in state_order:
                            state_index = state_order.index(current_label)
                        else:
                            state_index = 0
                    else:
                        state_index = 0

                    current_payload = _payload_for_state(
                        state_order,
                        state_index,
                        channel_info,
                        channel_text,
                        now_dt,
                    )
                    _warn_on_non_ascii_payload(current_payload, state_order[state_index])
                    display_state = _prepare_display_state(
                        current_payload.line1,
                        current_payload.line2,
                        current_payload.scroll_ms,
                    )
                    rotation_deadline = now + ROTATION_SECONDS

                    if len(state_order) > 1:
                        next_index = (state_index + 1) % len(state_order)
                        next_payload = _payload_for_state(
                            state_order,
                            next_index,
                            channel_info,
                            channel_text,
                            now_dt,
                        )
                        _warn_on_non_ascii_payload(
                            next_payload, state_order[next_index]
                        )
                        next_display_state = _prepare_display_state(
                            next_payload.line1,
                            next_payload.line2,
                            next_payload.scroll_ms,
                        )
                    else:
                        next_display_state = None

                if lcd is None:
                    lcd = _initialize_lcd()
                    frame_writer = LCDFrameWriter(lcd, history_recorder=history_recorder)
                    health.record_success()

                if display_state and frame_writer:
                    scroll_scheduler.sleep_until_ready()
                    frame_timestamp = datetime.now(datetime_timezone.utc)
                    label = state_order[state_index] if state_order else None
                    display_state, write_success = _advance_display(
                        display_state,
                        frame_writer,
                        label=label,
                        timestamp=frame_timestamp,
                    )
                    next_scroll_sec = display_state.scroll_sec
                    if write_success:
                        health.record_success()
                        if lcd and watchdog.tick():
                            lcd.reset()
                            watchdog.reset()
                    else:
                        if lcd is not None and frame_writer.lcd is None:
                            lcd = None
                            frame_writer = LCDFrameWriter(
                                None, history_recorder=history_recorder
                            )
                            display_state = None
                            next_display_state = None
                            next_scroll_sec = DEFAULT_FALLBACK_SCROLL_SEC
                        delay = health.record_failure()
                        time.sleep(delay)
                    scroll_scheduler.advance(
                        next_scroll_sec or DEFAULT_FALLBACK_SCROLL_SEC
                    )
                else:
                    scroll_scheduler.advance(DEFAULT_FALLBACK_SCROLL_SEC)
                    scroll_scheduler.sleep_until_ready()

                if time.monotonic() >= rotation_deadline:
                    if state_order:
                        state_index = (state_index + 1) % len(state_order)
                    if len(state_order) > 1:
                        display_state = next_display_state

                        # Prepare the following state in advance for predictable timing.
                        channel_info, channel_text = _load_channel_states(now_dt)
                        next_index = (state_index + 1) % len(state_order)
                        next_payload = _payload_for_state(
                            state_order,
                            next_index,
                            channel_info,
                            channel_text,
                            now_dt,
                        )
                        next_display_state = _prepare_display_state(
                            next_payload.line1,
                            next_payload.line2,
                            next_payload.scroll_ms,
                        )
                    else:
                        channel_info, channel_text = _load_channel_states(now_dt)
                        current_payload = _payload_for_state(
                            state_order,
                            state_index,
                            channel_info,
                            channel_text,
                            now_dt,
                        )
                        display_state = _prepare_display_state(
                            current_payload.line1,
                            current_payload.line2,
                            current_payload.scroll_ms,
                        )
                        next_display_state = None
                    rotation_deadline = time.monotonic() + ROTATION_SECONDS
            except LCDUnavailableError as exc:
                logger.warning("LCD unavailable: %s", exc)
                lcd = None
                frame_writer = LCDFrameWriter(None, history_recorder=history_recorder)
                display_state = None
                next_display_state = None
                delay = health.record_failure()
                time.sleep(delay)
            except Exception as exc:
                logger.warning("LCD update failed: %s", exc)
                _blank_display(lcd)
                lcd = None
                display_state = None
                next_display_state = None
                frame_writer = LCDFrameWriter(None, history_recorder=history_recorder)
                delay = health.record_failure()
                time.sleep(delay)

    finally:
        _blank_display(lcd)
        _reset_shutdown_flag()
        _reset_event_interrupt_flag()


if __name__ == "__main__":  # pragma: no cover - script entry point
    main()
