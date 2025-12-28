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
from datetime import datetime
from decimal import Decimal, InvalidOperation
from glob import glob
from pathlib import Path
from typing import Callable, NamedTuple
from itertools import cycle, islice

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
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
TIMING_ANCHOR_FILE = WORK_DIR / "lcd-timing.json"

LOGS_DIR.mkdir(parents=True, exist_ok=True)
WORK_DIR.mkdir(parents=True, exist_ok=True)

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

try:
    GAP_ANIMATION_FRAMES = default_tree_frames()
except AnimationLoadError:
    logger.debug("Falling back to blank animation frames", exc_info=True)
    GAP_ANIMATION_FRAMES = [" " * (LCD_COLUMNS * LCD_ROWS)]

GAP_ANIMATION_CYCLE = cycle(GAP_ANIMATION_FRAMES)


class FateDeck:
    """Shuffle and draw from a 54-card fate deck."""

    suits = ("D", "H", "C", "V")
    values = ("A", "2", "3", "4", "5", "6", "7", "8", "9", "X", "J", "Q", "K")
    jokers = ("XX", "XY")

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


class LCDFrameWriter:
    """Write full LCD frames with retry and batching."""

    def __init__(
        self, lcd: CharLCD1602 | None, *, work_file: Path = WORK_FILE
    ) -> None:
        self.lcd = lcd
        self.work_file = work_file

    def write(self, line1: str, line2: str) -> bool:
        row1 = line1.ljust(LCD_COLUMNS)[:LCD_COLUMNS]
        row2 = line2.ljust(LCD_COLUMNS)[:LCD_COLUMNS]

        if self.lcd is None:
            _write_work_display(row1, row2, target=self.work_file)
            return False

        try:
            self.lcd.write_frame(row1, row2, retries=1)
        except Exception as exc:
            logger.warning(
                "LCD write failed; writing to fallback file: %s", exc, exc_info=True
            )
            _write_work_display(row1, row2, target=self.work_file)
            self.lcd = None
            return False

        return True


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


def _current_boot_id(boot_id_path: Path = Path("/proc/sys/kernel/random/boot_id")) -> str | None:
    try:
        return boot_id_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


class TimingAnchor(NamedTuple):
    """Persist a timing offset relative to the system clock."""

    boot_id: str
    offset: float

    @classmethod
    def load(
        cls, path: Path, *, expected_boot: str | None
    ) -> "TimingAnchor | None":
        if not path.exists() or not expected_boot:
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            boot_id = str(data["boot_id"])
            offset = float(data["offset"]) % 1.0
        except Exception:
            logger.debug("Failed to read LCD timing anchor", exc_info=True)
            return None

        if boot_id != expected_boot:
            try:
                path.unlink()
            except OSError:
                logger.debug("Failed to remove stale LCD timing anchor", exc_info=True)
            return None

        if math.isnan(offset):
            return None

        return cls(boot_id=boot_id, offset=offset)

    def persist(self, path: Path) -> None:
        try:
            payload = {"boot_id": self.boot_id, "offset": self.offset % 1.0}
            path.write_text(json.dumps(payload), encoding="utf-8")
        except OSError:
            logger.debug("Failed to persist LCD timing anchor", exc_info=True)

    def alignment_delay(self, now: float | None = None) -> float:
        clock = time.monotonic if now is None else lambda: now
        fractional = clock() % 1.0
        delay = (self.offset - fractional) % 1.0
        return 0.0 if math.isclose(delay, 0.0, abs_tol=0.001) else delay


def _capture_timing_anchor(
    *,
    path: Path,
    boot_id: str | None,
    existing: TimingAnchor | None,
    clock_source=time.monotonic,
) -> TimingAnchor | None:
    if existing or not boot_id:
        return existing

    anchor = TimingAnchor(boot_id=boot_id, offset=clock_source() % 1.0)
    anchor.persist(path)
    return anchor


def _request_shutdown(signum, frame) -> None:  # pragma: no cover - signal handler
    """Mark the loop for shutdown when the process receives a signal."""

    global _SHUTDOWN_REQUESTED
    _SHUTDOWN_REQUESTED = True


def _shutdown_requested() -> bool:
    return _SHUTDOWN_REQUESTED


def _reset_shutdown_flag() -> None:
    global _SHUTDOWN_REQUESTED
    _SHUTDOWN_REQUESTED = False


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
    state: DisplayState, frame_writer: LCDFrameWriter
) -> tuple[DisplayState, bool]:
    if _shutdown_requested():
        return state, True

    segment1 = state.pad1[state.index1 : state.index1 + LCD_COLUMNS]
    segment2 = state.pad2[state.index2 : state.index2 + LCD_COLUMNS]

    write_required = segment1 != state.last_segment1 or segment2 != state.last_segment2
    write_success = True
    if write_required:
        write_success = frame_writer.write(
            segment1.ljust(LCD_COLUMNS), segment2.ljust(LCD_COLUMNS)
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
    clock_cycle = 0
    health = LCDHealthMonitor()
    watchdog = LCDWatchdog()
    frame_writer: LCDFrameWriter = LCDFrameWriter(None)

    boot_id = _current_boot_id()
    timing_anchor = TimingAnchor.load(TIMING_ANCHOR_FILE, expected_boot=boot_id)
    anchor_applied = False

    def _apply_timing_anchor_once() -> None:
        nonlocal anchor_applied
        if anchor_applied or timing_anchor is None:
            return

        delay = timing_anchor.alignment_delay()
        if delay > 0:
            logger.debug("Applying LCD timing anchor delay: %.3fs", delay)
            time.sleep(delay)
        anchor_applied = True

    signal.signal(signal.SIGTERM, _request_shutdown)
    signal.signal(signal.SIGINT, _request_shutdown)
    signal.signal(signal.SIGHUP, _request_shutdown)

    try:
        try:
            _apply_timing_anchor_once()
            lcd = _initialize_lcd()
            frame_writer = LCDFrameWriter(lcd)
            health.record_success()
            timing_anchor = _capture_timing_anchor(
                path=TIMING_ANCHOR_FILE,
                boot_id=boot_id,
                existing=timing_anchor,
            )
        except LCDUnavailableError as exc:
            logger.warning("LCD unavailable during startup: %s", exc)
        except Exception as exc:
            logger.warning("LCD startup failed: %s", exc, exc_info=True)

        while True:
            if _handle_shutdown_request(lcd):
                break

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
                            return low_payload
                        if _lcd_clock_enabled():
                            use_fahrenheit = clock_cycle % 2 == 0
                            line1, line2, speed, _ = _clock_payload(
                                datetime.now(), use_fahrenheit=use_fahrenheit
                            )
                            clock_cycle += 1
                            return LockPayload(line1, line2, speed)
                        return LockPayload("", "", DEFAULT_SCROLL_MS)

                    current_payload = _payload_for_state(state_index)
                    display_state = _prepare_display_state(
                        current_payload.line1,
                        current_payload.line2,
                        current_payload.scroll_ms,
                    )
                    rotation_deadline = now + ROTATION_SECONDS

                    next_index = (state_index + 1) % len(state_order)
                    next_payload = _payload_for_state(next_index)
                    next_display_state = _prepare_display_state(
                        next_payload.line1,
                        next_payload.line2,
                        next_payload.scroll_ms,
                    )

                if lcd is None:
                    _apply_timing_anchor_once()
                    lcd = _initialize_lcd()
                    frame_writer = LCDFrameWriter(lcd)
                    health.record_success()
                    timing_anchor = _capture_timing_anchor(
                        path=TIMING_ANCHOR_FILE,
                        boot_id=boot_id,
                        existing=timing_anchor,
                    )

                if display_state and frame_writer:
                    scroll_scheduler.sleep_until_ready()
                    display_state, write_success = _advance_display(
                        display_state, frame_writer
                    )
                    if write_success:
                        health.record_success()
                        if lcd and watchdog.tick():
                            lcd.reset()
                            watchdog.reset()
                    else:
                        if lcd is not None and frame_writer.lcd is None:
                            lcd = None
                            frame_writer = LCDFrameWriter(None)
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
                frame_writer = LCDFrameWriter(None)
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
                frame_writer = LCDFrameWriter(None)
                delay = health.record_failure()
                time.sleep(delay)

    finally:
        _blank_display(lcd)
        _reset_shutdown_flag()


if __name__ == "__main__":  # pragma: no cover - script entry point
    main()
