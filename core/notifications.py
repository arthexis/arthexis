"""Simple notification helper for a 16x2 LCD display.

Messages are written to a lock file read by an independent service that
updates the LCD. If writing to the lock file fails, a Windows
notification or log entry is used as a fallback. Each line is truncated
to 64 characters; scrolling is handled by the LCD service.
"""
from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path

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
    """Write notifications to a lock file or fall back to GUI/log output."""

    def __init__(self, lock_file: Path | None = None) -> None:
        base_dir = Path(__file__).resolve().parents[1]
        self.lock_file = lock_file or base_dir / "locks" / "lcd_screen.lck"
        self.lock_file.parent.mkdir(parents=True, exist_ok=True)
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

    def _write_lock_file(self, subject: str, body: str) -> None:
        self.lock_file.write_text(f"{subject}\n{body}\n", encoding="utf-8")

    def send(self, subject: str, body: str = "") -> bool:
        """Store *subject* and *body* in ``lcd_screen.lck`` when available.

        The method truncates each line to 64 characters. If the lock file is
        missing or writing fails, a GUI/log notification is used instead. In
        either case the function returns ``True`` so callers do not keep
        retrying in a loop when only the fallback is available.
        """

        if self.lock_file.exists():
            try:
                self._write_lock_file(subject[:64], body[:64])
                return True
            except Exception as exc:  # pragma: no cover - filesystem dependent
                logger.warning("LCD lock file write failed: %s", exc)
        else:
            logger.debug("LCD lock file missing; using fallback notification")
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
                        "Arthexis", f"{subject}\n{body}", duration=6, threaded=True
                    )
                    return
                except Exception as exc:  # pragma: no cover - depends on platform
                    logger.warning("Windows toast notification failed: %s", exc)
                    # Disable further toast attempts; the log fallback will be used
                    # instead to avoid repeated errors in headless environments.
                    self._toaster = None
            if plyer_notification:
                try:  # pragma: no cover - depends on platform
                    plyer_notification.notify(
                        title="Arthexis", message=f"{subject}\n{body}", timeout=6
                    )
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
