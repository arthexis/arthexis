"""Simple notification helper for a 16x2 LCD display.

Messages are written to a lock file read by an independent service that
updates the LCD. If writing to the lock file fails, a Windows
notification or log entry is used as a fallback. Each line is truncated
to 64 characters; scrolling is handled by the LCD service.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path

from apps.screens.startup_notifications import (
    LCD_HIGH_LOCK_FILE,
    LCD_LOW_LOCK_FILE,
    render_lcd_lock_file,
)

try:  # pragma: no cover - optional dependency
    from plyer import notification as plyer_notification
except Exception:  # pragma: no cover - plyer may not be installed
    plyer_notification = None

logger = logging.getLogger(__name__)


def get_base_dir() -> Path:
    """Return the project base directory used for shared lock files."""

    env_base = os.environ.get("ARTHEXIS_BASE_DIR")
    if env_base:
        return Path(env_base)

    try:  # pragma: no cover - depends on Django settings availability
        from django.conf import settings

        base_dir = getattr(settings, "BASE_DIR", None)
        if base_dir:
            return Path(base_dir)
    except Exception:
        pass

    cwd = Path.cwd()
    if (cwd / ".locks").exists():
        return cwd

    return Path(__file__).resolve().parents[1]


def supports_gui_toast() -> bool:
    """Return ``True`` when a GUI toast notification is available."""

    if not sys.platform.startswith("win"):
        return False
    notify = getattr(plyer_notification, "notify", None)
    return callable(notify)


class NotificationManager:
    """Write notifications to a lock file or fall back to GUI/log output."""

    def __init__(
        self,
        lock_file: Path | None = None,
        sticky_lock_file: Path | None = None,
    ) -> None:
        base_dir = get_base_dir()
        self.lock_file = lock_file or base_dir / ".locks" / LCD_LOW_LOCK_FILE
        self.sticky_lock_file = (
            sticky_lock_file or base_dir / ".locks" / LCD_HIGH_LOCK_FILE
        )
        self.lock_file.parent.mkdir(parents=True, exist_ok=True)
        self.sticky_lock_file.parent.mkdir(parents=True, exist_ok=True)
        # ``plyer`` is only available on Windows and can fail when used in
        # a non-interactive environment (e.g. service or CI).
        # Any failure will fall back to logging quietly.

    def _write_lock_file(self, subject: str, body: str, *, sticky: bool = False) -> None:
        payload = render_lcd_lock_file(subject=subject[:64], body=body[:64])
        target = self.sticky_lock_file if sticky else self.lock_file
        target.write_text(payload, encoding="utf-8")

    def send(self, subject: str, body: str = "", *, sticky: bool = False) -> bool:
        """Store *subject* and *body* in the LCD lock file when available.

        The method truncates each line to 64 characters. If the lock file is
        missing or writing fails, a GUI/log notification is used instead. In
        either case the function returns ``True`` so callers do not keep
        retrying in a loop when only the fallback is available.
        """

        try:
            self._write_lock_file(subject[:64], body[:64], sticky=sticky)
            return True
        except Exception as exc:  # pragma: no cover - filesystem dependent
            logger.warning("LCD lock file write failed: %s", exc)
            self._gui_display(subject, body)
            return True

    def send_async(self, subject: str, body: str = "", *, sticky: bool = False) -> None:
        """Dispatch :meth:`send` on a background thread."""

        def _send() -> None:
            try:
                self.send(subject, body, sticky=sticky)
            except Exception:
                # Notification failures shouldn't affect callers.
                pass

        threading.Thread(target=_send, daemon=True).start()

    # GUI/log fallback ------------------------------------------------
    def _gui_display(self, subject: str, body: str) -> None:
        if supports_gui_toast():
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


def notify(subject: str, body: str = "", *, sticky: bool = False) -> bool:
    """Convenience wrapper using the global :class:`NotificationManager`."""

    return manager.send(subject=subject, body=body, sticky=sticky)


def notify_async(subject: str, body: str = "", *, sticky: bool = False) -> None:
    """Run :func:`notify` without blocking the caller."""

    manager.send_async(subject=subject, body=body, sticky=sticky)
