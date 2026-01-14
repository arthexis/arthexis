"""Simple notification helper for a 16x2 LCD display.

Messages are written to a lock file read by an independent service that
updates the LCD. If writing to the lock file fails, a Windows
notification or log entry is used as a fallback. Each line is truncated
to 64 characters; scrolling is handled by the LCD service.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone as datetime_timezone
from enum import Enum
from pathlib import Path

from apps.screens.startup_notifications import (
    LCD_CLOCK_LOCK_FILE,
    LCD_HIGH_LOCK_FILE,
    LCD_LOW_LOCK_FILE,
    LCD_UPTIME_LOCK_FILE,
    lcd_feature_enabled,
    render_lcd_lock_file,
)

try:  # pragma: no cover - optional dependency
    from plyer import notification as plyer_notification
except Exception:  # pragma: no cover - plyer may not be installed
    plyer_notification = None

logger = logging.getLogger(__name__)
EVENT_LOCK_PATTERN = re.compile(r"^lcd-event-(\\d+)\\.lck$")


class LcdChannel(str, Enum):
    HIGH = "high"
    LOW = "low"
    CLOCK = "clock"
    UPTIME = "uptime"


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

    DEFAULT_CHANNEL_FILES: dict[str, str] = {
        LcdChannel.LOW.value: LCD_LOW_LOCK_FILE,
        LcdChannel.HIGH.value: LCD_HIGH_LOCK_FILE,
        LcdChannel.CLOCK.value: LCD_CLOCK_LOCK_FILE,
        LcdChannel.UPTIME.value: LCD_UPTIME_LOCK_FILE,
    }

    def __init__(
        self,
        lock_dir: Path | None = None,
    ) -> None:
        base_dir = get_base_dir()
        self.lock_dir = lock_dir or (base_dir / ".locks")
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        # ``plyer`` is only available on Windows and can fail when used in
        # a non-interactive environment (e.g. service or CI).
        # Any failure will fall back to logging quietly.

    @staticmethod
    def _normalize_channel_type(channel_type: str | None, *, sticky: bool = False) -> str:
        default_type = LcdChannel.HIGH.value if sticky else LcdChannel.LOW.value
        normalized = (channel_type or default_type).strip().lower()
        if not normalized:
            return LcdChannel.LOW.value
        return normalized

    @staticmethod
    def _normalize_channel_num(channel_num: int | str | None) -> int:
        try:
            normalized = int(channel_num) if channel_num is not None else 0
        except (TypeError, ValueError):
            return 0
        return normalized if normalized >= 0 else 0

    def get_target_lock_file(
        self,
        *,
        channel_type: str | None,
        channel_num: int | str | None,
        sticky: bool = False,
    ) -> Path:
        """Return the lock file path for the requested LCD channel."""

        normalized_type = self._normalize_channel_type(channel_type, sticky=sticky)
        normalized_num = self._normalize_channel_num(channel_num)
        filename = self.DEFAULT_CHANNEL_FILES.get(
            normalized_type, f"lcd-{normalized_type}"
        )
        if normalized_num != 0:
            filename = f"{filename}-{normalized_num}"
        return self.lock_dir / filename

    def _target_lock_file(
        self,
        *,
        channel_type: str | None,
        channel_num: int | str | None,
        sticky: bool = False,
    ) -> Path:
        """Deprecated wrapper for :meth:`get_target_lock_file`.

        Kept for backward compatibility with older callers that still
        reference the private method directly.
        """

        return self.get_target_lock_file(
            channel_type=channel_type,
            channel_num=channel_num,
            sticky=sticky,
        )

    def _write_lock_file(
        self,
        subject: str,
        body: str,
        *,
        sticky: bool = False,
        expires_at=None,
        channel_type: str | None = None,
        channel_num: int | str | None = None,
    ) -> None:
        payload = render_lcd_lock_file(
            subject=subject[:64], body=body[:64], expires_at=expires_at
        )
        target = self.get_target_lock_file(
            channel_type=channel_type, channel_num=channel_num, sticky=sticky
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(payload, encoding="utf-8")

    def _next_event_id(self) -> int:
        existing_ids: set[int] = set()
        if self.lock_dir.exists():
            for path in self.lock_dir.iterdir():
                match = EVENT_LOCK_PATTERN.match(path.name)
                if match:
                    try:
                        existing_ids.add(int(match.group(1)))
                    except ValueError:
                        continue
        candidate = 0
        while candidate in existing_ids:
            candidate += 1
        return candidate

    def _event_lock_file(self, event_id: int | None = None) -> Path:
        event_num = self._next_event_id() if event_id is None else max(event_id, 0)
        return self.lock_dir / f"lcd-event-{event_num}.lck"

    def _signal_lcd_service(self) -> None:
        pid = _lcd_service_pid(self.lock_dir)
        if pid is None:
            return
        try:
            os.kill(pid, signal.SIGUSR1)
        except Exception:
            logger.debug("Unable to signal LCD service", exc_info=True)

    def send(
        self,
        subject: str,
        body: str = "",
        *,
        sticky: bool = False,
        expires_at=None,
        channel_type: str | None = None,
        channel_num: int | str | None = None,
    ) -> bool:
        """Store *subject* and *body* in the LCD lock file when available.

        The method truncates each line to 64 characters. If the lock file is
        missing or writing fails, a GUI/log notification is used instead. In
        either case the function returns ``True`` so callers do not keep
        retrying in a loop when only the fallback is available.
        """

        if not lcd_feature_enabled(self.lock_dir):
            self._gui_display(subject, body)
            return True

        try:
            self._write_lock_file(
                subject[:64],
                body[:64],
                sticky=sticky,
                expires_at=expires_at,
                channel_type=channel_type,
                channel_num=channel_num,
            )
            return True
        except Exception as exc:  # pragma: no cover - filesystem dependent
            logger.warning("LCD lock file write failed: %s", exc)
            self._gui_display(subject, body)
            return True

    def send_event(
        self,
        subject: str,
        body: str = "",
        *,
        duration: int = 30,
        event_id: int | None = None,
        expires_at=None,
    ) -> bool:
        """Write an LCD event lock file and signal the LCD service."""

        if not lcd_feature_enabled(self.lock_dir):
            self._gui_display(subject, body)
            return True

        if expires_at is None:
            expires_at = datetime.now(datetime_timezone.utc) + timedelta(seconds=duration)
        payload = render_lcd_lock_file(subject=subject[:64], body=body[:64], expires_at=expires_at)
        target = self._event_lock_file(event_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.write_text(payload, encoding="utf-8")
            self._signal_lcd_service()
            return True
        except Exception as exc:  # pragma: no cover - filesystem dependent
            logger.warning("LCD event lock file write failed: %s", exc)
            self._gui_display(subject, body)
            return True

    def send_event_async(
        self,
        subject: str,
        body: str = "",
        *,
        duration: int = 30,
        event_id: int | None = None,
        expires_at=None,
    ) -> None:
        """Dispatch :meth:`send_event` on a background thread."""

        def _send() -> None:
            try:
                self.send_event(
                    subject,
                    body,
                    duration=duration,
                    event_id=event_id,
                    expires_at=expires_at,
                )
            except Exception:
                pass

        threading.Thread(target=_send, daemon=True).start()

    def send_async(
        self,
        subject: str,
        body: str = "",
        *,
        sticky: bool = False,
        expires_at=None,
        channel_type: str | None = None,
        channel_num: int | str | None = None,
    ) -> None:
        """Dispatch :meth:`send` on a background thread."""

        def _send() -> None:
            try:
                self.send(
                    subject,
                    body,
                    sticky=sticky,
                    expires_at=expires_at,
                    channel_type=channel_type,
                    channel_num=channel_num,
                )
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


def notify(
    subject: str,
    body: str = "",
    *,
    sticky: bool = False,
    expires_at=None,
    channel_type: str | None = None,
    channel_num: int | str | None = None,
) -> bool:
    """Convenience wrapper using the global :class:`NotificationManager`."""

    return manager.send(
        subject=subject,
        body=body,
        sticky=sticky,
        expires_at=expires_at,
        channel_type=channel_type,
        channel_num=channel_num,
    )


def notify_async(
    subject: str,
    body: str = "",
    *,
    sticky: bool = False,
    expires_at=None,
    channel_type: str | None = None,
    channel_num: int | str | None = None,
) -> None:
    """Run :func:`notify` without blocking the caller."""

    manager.send_async(
        subject=subject,
        body=body,
        sticky=sticky,
        expires_at=expires_at,
        channel_type=channel_type,
        channel_num=channel_num,
    )


def _lcd_service_pid(lock_dir: Path) -> int | None:
    pid_file = lock_dir / "lcd.pid"
    if pid_file.exists():
        try:
            value = int(pid_file.read_text(encoding="utf-8").strip())
            if value > 1:
                return value
        except Exception:
            return None

    service_lock = lock_dir / "service.lck"
    if not service_lock.exists():
        return None
    try:
        service_name = service_lock.read_text(encoding="utf-8").strip()
    except Exception:
        return None
    if not service_name:
        return None
    unit_name = f"lcd-{service_name}.service"
    if not shutil.which("systemctl"):
        return None
    try:
        result = subprocess.run(
            ["systemctl", "show", unit_name, "--property=MainPID", "--value"],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        return None
    pid_text = (result.stdout or "").strip()
    if not pid_text:
        return None
    try:
        pid_value = int(pid_text)
        return pid_value if pid_value > 1 else None
    except ValueError:
        return None


def notify_event(
    subject: str,
    body: str = "",
    *,
    duration: int = 30,
    event_id: int | None = None,
    expires_at=None,
) -> bool:
    """Send an event notification to the LCD service."""

    return manager.send_event(
        subject=subject,
        body=body,
        duration=duration,
        event_id=event_id,
        expires_at=expires_at,
    )


def notify_event_async(
    subject: str,
    body: str = "",
    *,
    duration: int = 30,
    event_id: int | None = None,
    expires_at=None,
) -> None:
    """Send an event notification asynchronously."""

    manager.send_event_async(
        subject=subject,
        body=body,
        duration=duration,
        event_id=event_id,
        expires_at=expires_at,
    )
