"""Notification system for LCD and desktop alerts.

Notifications are queued and displayed sequentially on an attached
LCD1602 display.  If the display is unavailable a desktop notification
is attempted using :mod:`plyer`.  Each notification is shown for at
least six seconds before the next queued item is processed.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass

from .lcd import CharLCD1602, LCDUnavailableError

try:  # pragma: no cover - optional dependency
    from plyer import notification as plyer_notify
except Exception:  # pragma: no cover - plyer may not be installed
    plyer_notify = None

logger = logging.getLogger(__name__)


@dataclass
class Notification:
    """Information about a notification to be displayed."""

    line1: str
    line2: str = ""
    duration: float = 6.0


class NotificationManager:
    """Manage a queue of notifications for the LCD display."""

    def __init__(self) -> None:
        self.queue: queue.Queue[Notification] = queue.Queue()
        self.lcd = self._init_lcd()
        self.thread = threading.Thread(target=self._worker, daemon=True)
        self.thread.start()

    def _init_lcd(self):
        try:
            lcd = CharLCD1602()
            lcd.init_lcd()
            return lcd
        except LCDUnavailableError as exc:  # pragma: no cover - hardware dependent
            logger.warning("LCD not initialized: %s", exc)
        except Exception as exc:  # pragma: no cover - hardware dependent
            logger.warning("Unexpected LCD error: %s", exc)
        return None

    def send(self, line1: str, line2: str = "", duration: float = 6.0) -> None:
        """Queue a new notification, adapting text to fit the screen."""

        full = f"{line1}{line2}"
        if not (len(line1) <= 16 and len(line2) <= 16 and len(full) <= 32):
            if len(full) <= 16:
                line1, line2 = full, ""
            elif len(full) <= 32:
                line1, line2 = full[:16], full[16:]
            else:
                line1, line2 = full[:16], full[16:]

        self.queue.put(Notification(line1=line1, line2=line2, duration=duration))

    def _worker(self) -> None:  # pragma: no cover - background thread
        while True:
            note = self.queue.get()
            try:
                self._display(note)
            finally:
                self.queue.task_done()

    # Display helpers -------------------------------------------------
    def _display(self, note: Notification) -> None:
        duration = max(note.duration, 6)
        if self.lcd:
            self._lcd_display(note, duration)
        else:
            self._gui_display(note)
            time.sleep(duration)
    def _lcd_display(self, note: Notification, duration: float) -> None:
        try:
            full = f"{note.line1}{note.line2}".strip()
            if (
                len(note.line1) <= 16
                and len(note.line2) <= 16
                and len(full) <= 32
            ):
                self.lcd.clear()
                self.lcd.write(0, 0, note.line1.ljust(16))
                if note.line2:
                    self.lcd.write(0, 1, note.line2.ljust(16))
                time.sleep(duration)
            else:
                top = full[:16]
                bottom = full[16:]
                scroll = bottom + " " * 4
                self.lcd.clear()
                self.lcd.write(0, 0, top.ljust(16))
                if not scroll:
                    time.sleep(duration)
                else:
                    end = time.time() + duration
                    idx = 0
                    while time.time() < end:
                        seg = (scroll + scroll[:16])[idx: idx + 16]
                        self.lcd.write(0, 1, seg.ljust(16))
                        time.sleep(0.5)
                        idx = (idx + 1) % len(scroll)
        except Exception as exc:  # pragma: no cover - hardware dependent
            logger.warning("LCD display failed: %s", exc)
            self.lcd = None
            self._gui_display(note)
            time.sleep(duration)

    def _gui_display(self, note: Notification) -> None:
        if plyer_notify:
            try:  # pragma: no cover - depends on platform
                plyer_notify.notify(title=note.line1, message=note.line2)
                return
            except Exception as exc:  # pragma: no cover - depends on platform
                logger.warning("GUI notification failed: %s", exc)
        logger.info("%s %s", note.line1, note.line2)


# Global manager used throughout the project
manager = NotificationManager()


def notify(line1: str, line2: str = "", duration: float = 6.0) -> None:
    """Queue a notification using the global manager."""

    manager.send(line1=line1, line2=line2, duration=duration)
