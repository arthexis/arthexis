"""Standalone LCD screen updater with predictable rotations.

The script polls ``.locks/lcd-high`` and ``.locks/lcd-low`` for payload
text and writes it to the attached LCD1602 display. The screen rotates
every 10 seconds across three states in a fixed order: High, Low, and
Time/Temp. Each row scrolls independently when it exceeds 16 characters;
shorter rows remain static.
"""

from __future__ import annotations

import json
import logging
import math
import os
import random
import signal
import time
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
HISTORY_DIR = BASE_DIR / "works"
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

from apps.screens.lcd import CharLCD1602, LCDUnavailableError
from apps.screens.animations import AnimationLoadError, default_tree_frames
from apps.screens.history import LCDHistoryRecorder
from apps.screens.startup_notifications import (
    LCD_HIGH_LOCK_FILE,
    LCD_LOW_LOCK_FILE,
    read_lcd_lock_file,
)

logger = logging.getLogger(__name__)
LOCK_DIR = BASE_DIR / ".locks"
HIGH_LOCK_FILE = LOCK_DIR / LCD_HIGH_LOCK_FILE
LOW_LOCK_FILE = LOCK_DIR / LCD_LOW_LOCK_FILE
DEFAULT_SCROLL_MS = 1000
MIN_SCROLL_MS = 50
SCROLL_PADDING = 3
LCD_COLUMNS = CharLCD1602.columns
LCD_ROWS = CharLCD1602.rows
CLOCK_TIME_FORMAT = "%p %I:%M"
CLOCK_DATE_FORMAT = "%Y-%m-%d %a"
ROTATION_SECONDS = 10
GAP_ANIMATION_FRAMES_PER_PAYLOAD = 4
GAP_ANIMATION_SCROLL_MS = 600
SUITE_UPTIME_LOCK_NAME = "suite_uptime.lck"
SUITE_UPTIME_LOCK_MAX_AGE = timedelta(minutes=10)

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
        lcd: CharLCD1602 | None,
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


def _read_lock_file(lock_file: Path) -> LockPayload:
    payload = read_lcd_lock_file(lock_file)
    if payload is None:
        return LockPayload("", "", DEFAULT_SCROLL_MS)
    return LockPayload(payload.subject, payload.body, DEFAULT_SCROLL_MS)


def _payload_has_text(payload: LockPayload) -> bool:
    return bool(payload.line1.strip() or payload.line2.strip())


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
    scroll_ms: int | None = None,
    frames_per_payload: int | None = None,
) -> LockPayload:
    if _payload_has_text(payload):
        return payload

    return _animation_payload(
        frame_cycle,
        frames_per_payload=frames_per_payload or GAP_ANIMATION_FRAMES_PER_PAYLOAD,
        scroll_ms=scroll_ms or GAP_ANIMATION_SCROLL_MS,
    )


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


def _format_uptime_label(seconds: int | None) -> str | None:
    components = _uptime_components(seconds)
    if components is None:
        return None

    days, hours, minutes = components
    return f"{days}d{hours}h{minutes}m"


def _refresh_uptime_payload(
    payload: LockPayload, *, base_dir: Path = BASE_DIR, now: datetime | None = None
) -> LockPayload:
    if not payload.line1.startswith("UP "):
        return payload

    uptime_label = _format_uptime_label(_uptime_seconds(base_dir, now=now))
    if not uptime_label:
        return payload

    suffix = payload.line1[3:].strip()
    role_suffix = suffix.split(maxsplit=1)[1].strip() if " " in suffix else ""
    subject = f"UP {uptime_label}"
    if role_suffix:
        subject = f"{subject} {role_suffix}"

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
                return label

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
        return f"{value:.1f}C"
    return None


def _clock_payload(
    now: datetime,
    *,
    use_fahrenheit: bool = False,
    fate_deck: FateDeck | None = None,
    choose_fate: Callable[[], bool] | None = None,
) -> tuple[str, str, int, str]:
    temperature = _lcd_temperature_label()
    if temperature and use_fahrenheit:
        unit = temperature[-1].upper()
        if unit == "C":
            try:
                temp_value = Decimal(temperature[:-1])
            except (InvalidOperation, ValueError):
                temp_value = None
            if temp_value is not None:
                temperature = f"{(temp_value * Decimal('9') / Decimal('5') + Decimal('32')):.1f}F"
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
_PAUSE_REQUESTED = False


def _request_shutdown(signum, frame) -> None:  # pragma: no cover - signal handler
    """Mark the loop for shutdown when the process receives a signal."""

    global _SHUTDOWN_REQUESTED
    _SHUTDOWN_REQUESTED = True


def _shutdown_requested() -> bool:
    return _SHUTDOWN_REQUESTED


def _reset_shutdown_flag() -> None:
    global _SHUTDOWN_REQUESTED
    _SHUTDOWN_REQUESTED = False


def _request_pause(signum, frame) -> None:  # pragma: no cover - signal handler
    """Pause LCD updates so another process can take over."""

    global _PAUSE_REQUESTED
    _PAUSE_REQUESTED = True


def _request_resume(signum, frame) -> None:  # pragma: no cover - signal handler
    """Resume LCD updates after a pause."""

    global _PAUSE_REQUESTED
    _PAUSE_REQUESTED = False


def _pause_requested() -> bool:
    return _PAUSE_REQUESTED


def _reset_pause_flag() -> None:
    global _PAUSE_REQUESTED
    _PAUSE_REQUESTED = False


def _blank_display(lcd: CharLCD1602 | None) -> None:
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


def _handle_shutdown_request(lcd: CharLCD1602 | None) -> bool:
    """Blank the display and signal the loop to exit when shutting down."""

    if not _shutdown_requested():
        return False

    _blank_display(lcd)
    return True


def _handle_pause_request(lcd: CharLCD1602 | None) -> tuple[CharLCD1602 | None, bool]:
    """Blank the display when pausing so other processes start cleanly."""

    if not _pause_requested():
        return lcd, False

    _blank_display(lcd)
    return None, True


def _display(
    lcd: CharLCD1602 | None, line1: str, line2: str, scroll_ms: int
) -> None:
    state = _prepare_display_state(line1, line2, scroll_ms)
    _advance_display(state, LCDFrameWriter(lcd))


def _prepare_display_state(line1: str, line2: str, scroll_ms: int) -> DisplayState:
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


def _initialize_lcd(reset: bool = True) -> CharLCD1602:
    lcd = CharLCD1602()
    lcd.init_lcd()
    if reset:
        lcd.reset()
    return lcd


def main() -> None:  # pragma: no cover - hardware dependent
    lcd = None
    display_state: DisplayState | None = None
    next_display_state: DisplayState | None = None
    high_payload = LockPayload("", "", DEFAULT_SCROLL_MS)
    low_payload = LockPayload("", "", DEFAULT_SCROLL_MS)
    high_mtime = 0.0
    low_mtime = 0.0
    rotation_deadline = 0.0
    scroll_scheduler = ScrollScheduler()
    state_order = ("high", "low", "clock")
    state_index = 0
    history_recorder = LCDHistoryRecorder(base_dir=BASE_DIR, history_dir_name="works")
    clock_cycle = 0
    health = LCDHealthMonitor()
    watchdog = LCDWatchdog()
    frame_writer: LCDFrameWriter = LCDFrameWriter(None)
    paused = False

    _clear_low_lock_file()

    signal.signal(signal.SIGTERM, _request_shutdown)
    signal.signal(signal.SIGINT, _request_shutdown)
    signal.signal(signal.SIGHUP, _request_shutdown)
    signal.signal(signal.SIGUSR1, _request_pause)
    signal.signal(signal.SIGUSR2, _request_resume)

    try:
        try:
            lcd = _initialize_lcd()
            frame_writer = LCDFrameWriter(lcd)
            health.record_success()
        except LCDUnavailableError as exc:
            logger.warning("LCD unavailable during startup: %s", exc)
        except Exception as exc:
            logger.warning("LCD startup failed: %s", exc, exc_info=True)

        while True:
            if _handle_shutdown_request(lcd):
                break

            lcd, paused = _handle_pause_request(lcd)
            if paused:
                frame_writer = LCDFrameWriter(None)
                display_state = None
                next_display_state = None
                time.sleep(0.1)
                continue

            try:
                now = time.monotonic()

                if display_state is None or now >= rotation_deadline:
                    high_available = True
                    try:
                        high_stat = HIGH_LOCK_FILE.stat()
                        if high_stat.st_mtime != high_mtime:
                            high_payload = _read_lock_file(HIGH_LOCK_FILE)
                            high_mtime = high_stat.st_mtime
                    except OSError:
                        high_payload = LockPayload("", "", DEFAULT_SCROLL_MS)
                        high_mtime = 0.0
                        high_available = False

                    low_available = True
                    try:
                        low_stat = LOW_LOCK_FILE.stat()
                        if low_stat.st_mtime != low_mtime:
                            low_payload = _read_lock_file(LOW_LOCK_FILE)
                            low_mtime = low_stat.st_mtime
                    except OSError:
                        low_payload = LockPayload("", "", DEFAULT_SCROLL_MS)
                        low_mtime = 0.0
                        low_available = False

                    low_payload = _select_low_payload(
                        low_payload,
                        frame_cycle=GAP_ANIMATION_CYCLE,
                        scroll_ms=GAP_ANIMATION_SCROLL_MS,
                    )
                    low_available = _payload_has_text(low_payload)

                    previous_order = state_order
                    if high_available:
                        state_order = ("high", "low", "clock") if low_available else ("high", "clock")
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

                    def _payload_for_state(index: int) -> LockPayload:
                        nonlocal clock_cycle
                        state_label = state_order[index]
                        if state_label == "high":
                            return high_payload
                        if state_label == "low":
                            return _refresh_uptime_payload(low_payload)
                        if _lcd_clock_enabled():
                            use_fahrenheit = clock_cycle % 2 == 0
                            line1, line2, speed, _ = _clock_payload(
                                datetime.now(), use_fahrenheit=use_fahrenheit
                            )
                            clock_cycle += 1
                            return LockPayload(line1, line2, speed)
                        return LockPayload("", "", DEFAULT_SCROLL_MS)

                    current_payload = _payload_for_state(state_index)
                    _warn_on_non_ascii_payload(current_payload, state_order[state_index])
                    display_state = _prepare_display_state(
                        current_payload.line1,
                        current_payload.line2,
                        current_payload.scroll_ms,
                    )
                    rotation_deadline = now + ROTATION_SECONDS

                    next_index = (state_index + 1) % len(state_order)
                    next_payload = _payload_for_state(next_index)
                    _warn_on_non_ascii_payload(next_payload, state_order[next_index])
                    next_display_state = _prepare_display_state(
                        next_payload.line1,
                        next_payload.line2,
                        next_payload.scroll_ms,
                    )

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
                        delay = health.record_failure()
                        time.sleep(delay)
                    scroll_scheduler.advance(display_state.scroll_sec or 0.5)
                else:
                    scroll_scheduler.advance(0.5)
                    scroll_scheduler.sleep_until_ready()

                if time.monotonic() >= rotation_deadline:
                    state_index = (state_index + 1) % len(state_order)
                    display_state = next_display_state

                    # Prepare the following state in advance for predictable timing.
                    high_payload = _read_lock_file(HIGH_LOCK_FILE)
                    low_payload = _read_lock_file(LOW_LOCK_FILE)
                    next_index = (state_index + 1) % len(state_order)
                    next_payload = _payload_for_state(next_index)
                    next_display_state = _prepare_display_state(
                        next_payload.line1,
                        next_payload.line2,
                        next_payload.scroll_ms,
                    )
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
        _reset_pause_flag()


if __name__ == "__main__":  # pragma: no cover - script entry point
    main()
