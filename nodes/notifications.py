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

    subject: str
    body: str = ""
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

    def send(self, subject: str, body: str = "", duration: float = 6.0) -> None:
        """Queue a new notification."""

        self.queue.put(Notification(subject=subject, body=body, duration=duration))

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
            subj, body = note.subject, note.body
            if len(subj) <= 16 and len(body) <= 16:
                self.lcd.clear()
                self.lcd.write(0, 0, subj.ljust(16))
                self.lcd.write(0, 1, body.ljust(16))
                time.sleep(duration)
            else:
                top_scroll = subj + " " * 4 if len(subj) > 16 else subj
                bottom_scroll = body + " " * 4 if len(body) > 16 else body
                end = time.time() + duration
                idx_top = idx_bottom = 0
                self.lcd.clear()
                while time.time() < end:
                    top_seg = (
                        (top_scroll + top_scroll[:16])[idx_top: idx_top + 16]
                        if len(subj) > 16
                        else subj.ljust(16)
                    )
                    bottom_seg = (
                        (bottom_scroll + bottom_scroll[:16])[idx_bottom: idx_bottom + 16]
                        if len(body) > 16
                        else body.ljust(16)
                    )
                    self.lcd.write(0, 0, top_seg.ljust(16))
                    self.lcd.write(0, 1, bottom_seg.ljust(16))
                    time.sleep(0.5)
                    if len(subj) > 16:
                        idx_top = (idx_top + 1) % len(top_scroll)
                    if len(body) > 16:
                        idx_bottom = (idx_bottom + 1) % len(bottom_scroll)
        except Exception as exc:  # pragma: no cover - hardware dependent
            logger.warning("LCD display failed: %s", exc)
            self.lcd = None
            self._gui_display(note)
            time.sleep(duration)

    def _gui_display(self, note: Notification) -> None:
        if plyer_notify:
            try:  # pragma: no cover - depends on platform
                plyer_notify.notify(title=note.subject, message=note.body)
                return
            except Exception as exc:  # pragma: no cover - depends on platform
                logger.warning("GUI notification failed: %s", exc)
        logger.info("%s %s", note.subject, note.body)


# Global manager used throughout the project
manager = NotificationManager()


def notify(subject: str, body: str = "", duration: float = 6.0) -> None:
    """Queue a notification using the global manager."""

    manager.send(subject=subject, body=body, duration=duration)
