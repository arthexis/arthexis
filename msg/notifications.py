"""Simple notification helper for a 16x2 LCD display.

Messages are written directly to the LCD. When the display is unavailable
 the message is shown using a Windows notification that auto-dismisses after
 six seconds or logged. Each line is truncated to 16 characters so that it
 fits the 16x2 hardware display.
"""
from __future__ import annotations

import logging
import sys
import threading

from nodes.lcd import CharLCD1602, LCDUnavailableError

try:  # pragma: no cover - optional dependency
    from win10toast import ToastNotifier
except Exception:  # pragma: no cover - win10toast may not be installed
    ToastNotifier = None
try:  # pragma: no cover - optional dependency
    from plyer import notification as plyer_notification
except Exception:  # pragma: no cover - plyer may not be installed
    plyer_notification = None

logger = logging.getLogger(__name__)


class NotificationManager:
    """Write notifications to the LCD or fall back to GUI/log output."""

    def __init__(self) -> None:
        # Attempt to initialise the LCD once during construction. If it fails
        # we remember that an attempt was made so that subsequent notifications
        # don't repeatedly retry and flood the logs.
        self.lcd = self._init_lcd()
        self._lcd_attempted = True

        # ``win10toast`` is only available on Windows and can fail when used in
        # a non-interactive environment (e.g. service or CI). Any failure will
        # disable further toast attempts so the application falls back to
        # logging quietly.
        self._toaster = None
        if sys.platform.startswith("win") and ToastNotifier:
            try:  # pragma: no cover - depends on platform
                self._toaster = ToastNotifier()
            except Exception as exc:  # pragma: no cover - depends on platform
                logger.warning("Windows toast notifier unavailable: %s", exc)
                self._toaster = None

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

        The method truncates each line to 16 characters. If the LCD is not
        available or writing fails a GUI/log notification is used instead.
        In either case the function returns ``True`` so callers do not keep
        retrying in a loop when only the fallback is available.
        """

        if not self.lcd and not getattr(self, "_lcd_attempted", False):
            self.lcd = self._init_lcd()
            self._lcd_attempted = True
        if self.lcd:
            try:
                self.lcd.clear()
                self.lcd.write(0, 0, subject[:16].ljust(16))
                self.lcd.write(0, 1, body[:16].ljust(16))
                return True
            except Exception as exc:  # pragma: no cover - hardware dependent
                logger.warning("LCD display failed: %s", exc)
                try:
                    self.lcd.reset()
                    self.lcd.clear()
                    self.lcd.write(0, 0, subject[:16].ljust(16))
                    self.lcd.write(0, 1, body[:16].ljust(16))
                    return True
                except Exception as exc2:  # pragma: no cover - hardware dependent
                    logger.warning("LCD reset failed: %s", exc2)
                    self.lcd = None
        # Even if the LCD is unavailable we still consider the notification
        # successfully handled once the GUI/log fallback runs to avoid callers
        # retrying in a loop.
        self._gui_display(subject, body)
        return True

    def send_async(self, subject: str, body: str = "") -> None:
        """Dispatch :meth:`send` on a background thread."""

        def _send() -> None:
            try:
                self.send(subject, body)
            except Exception:
                # Notification failures shouldn't affect callers.
                pass

        threading.Thread(target=_send, daemon=True).start()

    # GUI/log fallback ------------------------------------------------
    def _gui_display(self, subject: str, body: str) -> None:
        if sys.platform.startswith("win"):
            if self._toaster:
                try:  # pragma: no cover - depends on platform
                    self._toaster.show_toast(
                        "Arthexis", f"{subject}\n{body}", duration=6
                    )
                    return
                except Exception as exc:  # pragma: no cover - depends on platform
                    logger.warning("Windows toast notification failed: %s", exc)
                    # Disable further toast attempts; the log fallback will be used
                    # instead to avoid repeated errors in headless environments.
                    self._toaster = None
            elif plyer_notification:
                try:  # pragma: no cover - depends on platform
                    plyer_notification.notify(
                        title="Arthexis", message=f"{subject}\n{body}", timeout=6
                    )
                    return
                except Exception as exc:  # pragma: no cover - depends on platform
                    logger.warning("Windows notification failed: %s", exc)
        logger.info("%s %s", subject, body)


# Global manager used throughout the project
manager = NotificationManager()


def notify(subject: str, body: str = "") -> bool:
    """Convenience wrapper using the global :class:`NotificationManager`."""

    return manager.send(subject=subject, body=body)


def notify_async(subject: str, body: str = "") -> None:
    """Run :func:`notify` without blocking the caller."""

    manager.send_async(subject=subject, body=body)
