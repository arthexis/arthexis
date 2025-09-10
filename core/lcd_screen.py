"""Standalone LCD screen updater.

The script polls ``locks/lcd_screen.lck`` for up to two lines of text and
writes them to the attached LCD1602 display. If either line exceeds 16
characters the text scrolls horizontally. A third line in the lock file
can define the scroll speed in milliseconds per character (default 1000
ms).
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from nodes.lcd import CharLCD1602, LCDUnavailableError

logger = logging.getLogger(__name__)

LOCK_FILE = Path(__file__).resolve().parents[1] / "locks" / "lcd_screen.lck"
DEFAULT_SCROLL_MS = 1000


def _read_lock_file() -> tuple[str, str, int]:
    try:
        lines = LOCK_FILE.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return "", "", DEFAULT_SCROLL_MS
    line1 = lines[0][:64] if len(lines) > 0 else ""
    line2 = lines[1][:64] if len(lines) > 1 else ""
    try:
        speed = int(lines[2]) if len(lines) > 2 else DEFAULT_SCROLL_MS
    except ValueError:
        speed = DEFAULT_SCROLL_MS
    return line1, line2, speed


def _display(lcd: CharLCD1602, line1: str, line2: str, scroll_ms: int) -> None:
    scroll_sec = max(scroll_ms, 0) / 1000.0
    text1 = line1[:64]
    text2 = line2[:64]
    pad1 = text1 + " " * 16 if len(text1) > 16 else text1.ljust(16)
    pad2 = text2 + " " * 16 if len(text2) > 16 else text2.ljust(16)
    steps = max(len(pad1) - 15, len(pad2) - 15)
    for i in range(steps):
        segment1 = pad1[i : i + 16]
        segment2 = pad2[i : i + 16]
        lcd.write(0, 0, segment1.ljust(16))
        lcd.write(0, 1, segment2.ljust(16))
        time.sleep(scroll_sec)


def main() -> None:  # pragma: no cover - hardware dependent
    lcd = None
    last_mtime = 0.0
    while True:
        try:
            if LOCK_FILE.exists():
                mtime = LOCK_FILE.stat().st_mtime
                if mtime != last_mtime or lcd is None:
                    line1, line2, speed = _read_lock_file()
                    if lcd is None:
                        lcd = CharLCD1602()
                        lcd.init_lcd()
                    lcd.clear()
                    _display(lcd, line1, line2, speed)
                    last_mtime = mtime
        except LCDUnavailableError as exc:
            logger.warning("LCD unavailable: %s", exc)
            lcd = None
        except Exception as exc:
            logger.warning("LCD update failed: %s", exc)
            lcd = None
        time.sleep(0.5)


if __name__ == "__main__":  # pragma: no cover - script entry point
    main()
