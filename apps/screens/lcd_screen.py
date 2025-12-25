"""Standalone LCD screen updater with predictable rotations.

The script polls ``.locks/lcd-sticky`` and ``.locks/lcd-latest`` for
payload text and writes it to the attached LCD1602 display. The screen
rotates every 10 seconds across three states in a fixed order: Sticky,
Latest, and Time/Temp. Each row scrolls independently when it exceeds 16
characters; shorter rows remain static.
"""

from __future__ import annotations

import logging
import math
import os
import signal
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation
from glob import glob
from pathlib import Path
from typing import NamedTuple

from apps.core.notifications import get_base_dir
from apps.screens.lcd import CharLCD1602, LCDUnavailableError
from apps.screens.startup_notifications import (
    LCD_LATEST_LOCK_FILE,
    LCD_STICKY_LOCK_FILE,
    read_lcd_lock_file,
)

logger = logging.getLogger(__name__)

BASE_DIR = get_base_dir()
LOCK_DIR = BASE_DIR / ".locks"
STICKY_LOCK_FILE = LOCK_DIR / LCD_STICKY_LOCK_FILE
LATEST_LOCK_FILE = LOCK_DIR / LCD_LATEST_LOCK_FILE
DEFAULT_SCROLL_MS = 1000
MIN_SCROLL_MS = 50
SCROLL_PADDING = 3
LCD_COLUMNS = CharLCD1602.columns
LCD_ROWS = CharLCD1602.rows
CLOCK_TIME_FORMAT = "%p %I:%M"
CLOCK_DATE_FORMAT = "%Y-%m-%d %a"
ROTATION_SECONDS = 10


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

    def __init__(self, lcd: CharLCD1602) -> None:
        self.lcd = lcd

    def write(self, line1: str, line2: str) -> None:
        self.lcd.write_frame(line1, line2, retries=1)


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


def _lcd_clock_enabled() -> bool:
    raw = (os.getenv("DISABLE_LCD_CLOCK") or "").strip().lower()
    return raw not in {"1", "true", "yes", "on"}


def _lcd_temperature_label() -> str | None:
    label = _lcd_temperature_label_from_sensors()
    if label:
        return label
    return _lcd_temperature_label_from_sysfs()


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


def _clock_payload(now: datetime, *, use_fahrenheit: bool = False) -> tuple[str, str, int, str]:
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
    date_label = now.strftime(CLOCK_DATE_FORMAT)
    time_label = now.strftime(CLOCK_TIME_FORMAT)
    if temperature:
        time_label = f"{time_label} @ {temperature}"
    return (
        date_label,
        time_label,
        DEFAULT_SCROLL_MS,
        "clock",
    )


_SHUTDOWN_REQUESTED = False


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


def _display(lcd: CharLCD1602, line1: str, line2: str, scroll_ms: int) -> None:
    state = _prepare_display_state(line1, line2, scroll_ms)
    _advance_display(lcd, state, LCDFrameWriter(lcd))


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
    lcd: CharLCD1602, state: DisplayState, frame_writer: LCDFrameWriter
) -> DisplayState:
    if _shutdown_requested():
        return state

    segment1 = state.pad1[state.index1 : state.index1 + LCD_COLUMNS]
    segment2 = state.pad2[state.index2 : state.index2 + LCD_COLUMNS]

    write_required = segment1 != state.last_segment1 or segment2 != state.last_segment2
    if write_required:
        frame_writer.write(segment1.ljust(LCD_COLUMNS), segment2.ljust(LCD_COLUMNS))

    next_index1 = (state.index1 + 1) % state.steps1
    next_index2 = (state.index2 + 1) % state.steps2
    return state._replace(
        index1=next_index1,
        index2=next_index2,
        last_segment1=segment1,
        last_segment2=segment2,
    )


def main() -> None:  # pragma: no cover - hardware dependent
    lcd = None
    display_state: DisplayState | None = None
    next_display_state: DisplayState | None = None
    sticky_payload = LockPayload("", "", DEFAULT_SCROLL_MS)
    latest_payload = LockPayload("", "", DEFAULT_SCROLL_MS)
    sticky_mtime = 0.0
    latest_mtime = 0.0
    rotation_deadline = 0.0
    scroll_scheduler = ScrollScheduler()
    state_order = ("latest", "sticky", "clock")
    state_index = 0
    clock_cycle = 0
    health = LCDHealthMonitor()
    watchdog = LCDWatchdog()
    frame_writer: LCDFrameWriter | None = None

    signal.signal(signal.SIGTERM, _request_shutdown)
    signal.signal(signal.SIGINT, _request_shutdown)
    signal.signal(signal.SIGHUP, _request_shutdown)

    try:
        while True:
            if _handle_shutdown_request(lcd):
                break

            try:
                now = time.monotonic()

                if display_state is None or now >= rotation_deadline:
                    sticky_available = True
                    try:
                        sticky_stat = STICKY_LOCK_FILE.stat()
                        if sticky_stat.st_mtime != sticky_mtime:
                            sticky_payload = _read_lock_file(STICKY_LOCK_FILE)
                            sticky_mtime = sticky_stat.st_mtime
                    except OSError:
                        sticky_payload = LockPayload("", "", DEFAULT_SCROLL_MS)
                        sticky_mtime = 0.0
                        sticky_available = False

                    try:
                        latest_stat = LATEST_LOCK_FILE.stat()
                        if latest_stat.st_mtime != latest_mtime:
                            latest_payload = _read_lock_file(LATEST_LOCK_FILE)
                            latest_mtime = latest_stat.st_mtime
                    except OSError:
                        latest_payload = LockPayload("", "", DEFAULT_SCROLL_MS)
                        latest_mtime = 0.0

                    previous_order = state_order
                    if sticky_available:
                        state_order = ("latest", "sticky", "clock")
                    else:
                        state_order = ("latest", "clock")

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
                        if state_label == "sticky":
                            return sticky_payload
                        if state_label == "latest":
                            return latest_payload
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
                    lcd = CharLCD1602()
                    lcd.init_lcd()
                    frame_writer = LCDFrameWriter(lcd)
                    health.record_success()

                if display_state and frame_writer:
                    scroll_scheduler.sleep_until_ready()
                    display_state = _advance_display(lcd, display_state, frame_writer)
                    health.record_success()
                    if watchdog.tick():
                        lcd.reset()
                        watchdog.reset()
                    scroll_scheduler.advance(display_state.scroll_sec or 0.5)
                else:
                    scroll_scheduler.advance(0.5)
                    scroll_scheduler.sleep_until_ready()

                if time.monotonic() >= rotation_deadline:
                    state_index = (state_index + 1) % len(state_order)
                    display_state = next_display_state

                    # Prepare the following state in advance for predictable timing.
                    if lcd is not None:
                        sticky_payload = _read_lock_file(STICKY_LOCK_FILE)
                        latest_payload = _read_lock_file(LATEST_LOCK_FILE)
                    next_index = (state_index + 1) % len(state_order)
                    if lcd is not None:
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
                frame_writer = None
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
                frame_writer = None
                delay = health.record_failure()
                time.sleep(delay)

    finally:
        _blank_display(lcd)
        _reset_shutdown_flag()


if __name__ == "__main__":  # pragma: no cover - script entry point
    main()
