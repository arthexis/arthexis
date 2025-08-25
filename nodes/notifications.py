"""Simple notification helper for a 16x2 LCD display.

Messages are written directly to the LCD.  When the display is
unavailable the message is shown using a desktop notification (via
``plyer``) or logged.  Each line is truncated to 16 characters so that it
fits the 16x2 hardware display.
"""

from __future__ import annotations

import logging

from .lcd import CharLCD1602, LCDUnavailableError

try:  # pragma: no cover - optional dependency
    from plyer import notification as plyer_notify
except Exception:  # pragma: no cover - plyer may not be installed
    plyer_notify = None

logger = logging.getLogger(__name__)


class NotificationManager:
    """Write notifications to the LCD or fall back to GUI/log output."""

    def __init__(self) -> None:
        self.lcd = self._init_lcd()

    # LCD helpers -----------------------------------------------------
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

    def send(self, subject: str, body: str = "") -> bool:
        """Display *subject* and *body* and return ``True`` on success.

        The method truncates each line to 16 characters.  If the LCD is not
        available or writing fails a GUI notification is attempted instead
        and ``False`` is returned.
        """

        if not self.lcd:
            self.lcd = self._init_lcd()
        if self.lcd:
            try:
                self.lcd.clear()
                self.lcd.write(0, 0, subject[:16].ljust(16))
                self.lcd.write(0, 1, body[:16].ljust(16))
                return True
            except Exception as exc:  # pragma: no cover - hardware dependent
                logger.warning("LCD display failed: %s", exc)
                self.lcd = None
        self._gui_display(subject, body)
        return False

    # GUI/log fallback ------------------------------------------------
    def _gui_display(self, subject: str, body: str) -> None:
        if plyer_notify:
            try:  # pragma: no cover - depends on platform
                plyer_notify.notify(title=subject, message=body)
                return
            except Exception as exc:  # pragma: no cover - depends on platform
                logger.warning("GUI notification failed: %s", exc)
        logger.info("%s %s", subject, body)


# Global manager used throughout the project
manager = NotificationManager()


def notify(subject: str, body: str = "") -> bool:
    """Convenience wrapper using the global :class:`NotificationManager`."""

    return manager.send(subject=subject, body=body)
