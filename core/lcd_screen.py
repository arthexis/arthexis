"""Standalone LCD screen updater.

The script polls ``locks/lcd_screen.lck`` for up to two lines of text and
writes them to the attached LCD1602 display. If either line exceeds 16
characters the text scrolls horizontally. A third line in the lock file
can define the scroll speed in milliseconds per character (default 1000
ms). When the suite service stops or the OS schedules a shutdown/reboot
the updater temporarily overrides the lock file content to surface the
alert directly on the display.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

from core.notifications import get_base_dir
from nodes.lcd import CharLCD1602, LCDUnavailableError

logger = logging.getLogger(__name__)

BASE_DIR = get_base_dir()
LOCK_DIR = BASE_DIR / "locks"
LOCK_FILE = LOCK_DIR / "lcd_screen.lck"
SERVICE_LOCK_FILE = LOCK_DIR / "service.lck"
SHUTDOWN_SCHEDULE_FILE = Path("/run/systemd/shutdown/scheduled")
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


def _clear_lock_file() -> None:
    """Remove the LCD lock file after the payload has been consumed."""

    try:
        LOCK_FILE.unlink()
    except FileNotFoundError:
        return
    except OSError:
        # The updater should continue running even if the lock file cannot be
        # removed (for example, due to transient filesystem issues).
        logger.debug("Failed to clear LCD lock file", exc_info=True)


def _lock_file_matches(
    payload: tuple[str, str, int], expected_mtime: float
) -> bool:
    """Return True when the lock file still matches the consumed payload."""

    try:
        current_mtime = LOCK_FILE.stat().st_mtime
    except FileNotFoundError:
        return False
    except OSError:
        return False

    if current_mtime != expected_mtime:
        return False

    return _read_lock_file() == payload


def _read_service_name() -> str | None:
    try:
        raw = SERVICE_LOCK_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return raw or None


def _systemctl_status(service: str) -> str | None:
    if not service or shutil.which("systemctl") is None:
        return None
    try:
        result = subprocess.run(
            ["systemctl", "is-active", service],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        return None
    return result.stdout.strip() or None


def _suite_down_notice() -> tuple[str, str] | None:
    """Return a warning message when the suite service is not active."""

    service = _read_service_name()
    status = _systemctl_status(service or "")
    if status is None or status == "active":
        return None
    status_label = status.capitalize()
    service_label = service or "Suite"
    return (f"{service_label} offline", f"Status: {status_label}")


def _read_shutdown_schedule() -> dict[str, str] | None:
    try:
        content = SHUTDOWN_SCHEDULE_FILE.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        return None
    schedule: dict[str, str] = {}
    for line in content.splitlines():
        key, _, value = line.partition("=")
        if key and _:
            schedule[key.strip().upper()] = value.strip()
    return schedule or None


def _system_shutdown_notice() -> tuple[str, str] | None:
    """Return a warning message when the OS scheduled a shutdown/reboot."""

    schedule = _read_shutdown_schedule()
    if not schedule:
        return None

    normalized_mode = (schedule.get("MODE") or schedule.get("UNIT") or "").lower()
    if "reboot" in normalized_mode:
        title = "System reboot"
    elif "halt" in normalized_mode:
        title = "System halt"
    elif "power" in normalized_mode:
        title = "System poweroff"
    else:
        title = "System shutdown"

    eta_label = "Prepare system"
    usec_text = schedule.get("USEC")
    if usec_text:
        try:
            timestamp = datetime.fromtimestamp(int(usec_text) / 1_000_000)
        except (ValueError, OverflowError, OSError):
            timestamp = None
        if timestamp is not None:
            eta_label = timestamp.strftime("ETA %Y-%m-%d %H:%M")

    return title, eta_label


def _resolve_display_payload(
    lock_payload: tuple[str, str, int]
) -> tuple[str, str, int, str]:
    shutdown_notice = _system_shutdown_notice()
    if shutdown_notice:
        line1, line2 = shutdown_notice
        return line1, line2, DEFAULT_SCROLL_MS, "system-shutdown"

    suite_notice = _suite_down_notice()
    if suite_notice:
        line1, line2 = suite_notice
        return line1, line2, DEFAULT_SCROLL_MS, "suite-down"

    line1, line2, speed = lock_payload
    return line1, line2, speed, "lock-file"


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
    last_lock_mtime = 0.0
    lock_payload: tuple[str, str, int] = ("", "", DEFAULT_SCROLL_MS)
    last_display: tuple[str, str, int, str] | None = None
    while True:
        try:
            if LOCK_FILE.exists():
                mtime = LOCK_FILE.stat().st_mtime
                if mtime != last_lock_mtime:
                    lock_payload = _read_lock_file()
                    last_lock_mtime = mtime
            else:
                if last_lock_mtime != 0:
                    lock_payload = ("", "", DEFAULT_SCROLL_MS)
                last_lock_mtime = 0.0

            line1, line2, speed, source = _resolve_display_payload(lock_payload)
            current_display = (line1, line2, speed, source)
            if current_display != last_display or lcd is None:
                if lcd is None:
                    lcd = CharLCD1602()
                    lcd.init_lcd()
                lcd.clear()
                _display(lcd, line1, line2, speed)
                last_display = current_display
                if source == "lock-file" and _lock_file_matches(lock_payload, last_lock_mtime):
                    _clear_lock_file()
        except LCDUnavailableError as exc:
            logger.warning("LCD unavailable: %s", exc)
            lcd = None
        except Exception as exc:
            logger.warning("LCD update failed: %s", exc)
            lcd = None
        time.sleep(0.5)


if __name__ == "__main__":  # pragma: no cover - script entry point
    main()
