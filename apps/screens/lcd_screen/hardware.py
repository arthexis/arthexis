"""LCD hardware abstraction for the LCD screen service."""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone as datetime_timezone
from pathlib import Path

from apps.screens.history import LCDHistoryRecorder
from apps.screens.lcd import (
    CharLCD1602,
    LCDController,
    LCDUnavailableError,
    prepare_lcd_controller,
)

from .logging import WORK_FILE

logger = logging.getLogger(__name__)

LCD_COLUMNS = CharLCD1602.columns
LCD_ROWS = CharLCD1602.rows


def _write_work_display(line1: str, line2: str, *, target: Path = WORK_FILE) -> None:
    row1 = line1.ljust(LCD_COLUMNS)[:LCD_COLUMNS]
    row2 = line2.ljust(LCD_COLUMNS)[:LCD_COLUMNS]
    try:
        target.write_text(f"{row1}\n{row2}\n", encoding="utf-8")
    except Exception:
        logger.debug("Failed to write LCD fallback output", exc_info=True)


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
        if self.max_delay <= 0:
            return 0.0
        if self.base_delay <= 0:
            return self.max_delay
        max_multiplier = self.max_delay / self.base_delay
        if not math.isfinite(max_multiplier):
            return self.max_delay
        if max_multiplier <= 1:
            return self.max_delay
        exponent = self.failure_count - 1
        if exponent >= math.log2(max_multiplier):
            return self.max_delay
        return self.base_delay * (2**exponent)

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


def _initialize_lcd() -> LCDController:
    return prepare_lcd_controller()
