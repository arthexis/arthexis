"""Standalone LCD screen updater.

The script polls ``.locks/lcd_screen.lck`` for state and payload text and
writes it to the attached LCD1602 display. Each row scrolls independently
when it exceeds 16 characters; shorter rows remain static. Optional flag
lines in the lock file can define scroll speed in milliseconds per
character (default 1000 ms). When the suite service stops or the OS
schedules a shutdown/reboot the updater temporarily overrides the lock
file content to surface the alert directly on the display.
"""

from __future__ import annotations

import logging
import math
import os
import shutil
import signal
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

from apps.core.notifications import get_base_dir
from apps.screens.lcd import CharLCD1602, LCDUnavailableError
from apps.screens.startup_notifications import (
    LCD_LEGACY_FEATURE_LOCK,
    LCD_LOCK_FILE,
    LCD_STATE_DISABLED,
    ensure_lcd_lock_file,
    parse_lcd_flags,
    read_lcd_lock_file,
    render_lcd_lock_file,
)

logger = logging.getLogger(__name__)

BASE_DIR = get_base_dir()
LOCK_DIR = BASE_DIR / ".locks"
LOCK_FILE = LOCK_DIR / LCD_LOCK_FILE
SERVICE_LOCK_FILE = LOCK_DIR / "service.lck"
SHUTDOWN_SCHEDULE_FILE = Path("/run/systemd/shutdown/scheduled")
DEFAULT_SCROLL_MS = 1000
SCROLL_PADDING = 3
LCD_COLUMNS = CharLCD1602.columns
LCD_ROWS = CharLCD1602.rows
CLOCK_MESSAGE_SECONDS = 6
CLOCK_TIME_FORMAT = "%H:%M"
CLOCK_DATE_FORMAT = "%Y-%m-%d"


class LockPayload(NamedTuple):
    line1: str
    line2: str
    scroll_ms: int
    net_message: bool
    sticky: bool
    enabled: bool


class DisplayState(NamedTuple):
    pad1: str
    pad2: str
    steps1: int
    steps2: int
    index1: int
    index2: int
    scroll_sec: float
    cycle: int


def _read_lock_file() -> LockPayload:
    ensure_lcd_lock_file(LOCK_DIR)
    lock_data = read_lcd_lock_file(LOCK_FILE)
    if lock_data is None:
        return LockPayload("", "", DEFAULT_SCROLL_MS, False, False)

    enabled = lock_data.state != LCD_STATE_DISABLED
    net_message, scroll_ms, sticky = parse_lcd_flags(lock_data.flags)
    speed = scroll_ms if scroll_ms is not None else DEFAULT_SCROLL_MS
    if not enabled:
        return LockPayload("", "", DEFAULT_SCROLL_MS, False, False, False)
    return LockPayload(
        lock_data.subject,
        lock_data.body,
        speed,
        net_message,
        sticky,
        True,
    )


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


def _disable_lcd_feature(lock_dir: Path = LOCK_DIR) -> None:
    """Mark the LCD feature as disabled when the I2C bus is missing."""

    lock_file = lock_dir / LCD_LOCK_FILE
    try:
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        lock_data = read_lcd_lock_file(lock_file)
        payload = render_lcd_lock_file(
            state=LCD_STATE_DISABLED,
            subject=lock_data.subject if lock_data else "",
            body=lock_data.body if lock_data else "",
            flags=lock_data.flags if lock_data else None,
        )
        lock_file.write_text(payload, encoding="utf-8")
    except OSError:
        logger.debug("Failed to update LCD lock file state", exc_info=True)

    legacy_lock = lock_dir / LCD_LEGACY_FEATURE_LOCK
    try:
        legacy_lock.unlink()
    except FileNotFoundError:
        return
    except OSError:
        logger.debug("Failed to remove legacy LCD lock file", exc_info=True)


def _handle_lcd_failure(exc: Exception, lock_dir: Path = LOCK_DIR) -> bool:
    """Handle LCD errors and return True when the feature should be disabled."""

    if isinstance(exc, FileNotFoundError) and "/dev/i2c-1" in str(exc):
        logger.warning(
            "LCD update failed: %s; disabling lcd-screen feature", exc
        )
        _disable_lcd_feature(lock_dir)
        return True

    logger.warning("LCD update failed: %s", exc)
    return False


def _lock_file_matches(payload: LockPayload, expected_mtime: float) -> bool:
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
    lock_payload: LockPayload,
) -> tuple[str, str, int, str]:
    shutdown_notice = _system_shutdown_notice()
    if shutdown_notice:
        line1, line2 = shutdown_notice
        return line1, line2, DEFAULT_SCROLL_MS, "system-shutdown"

    suite_notice = _suite_down_notice()
    if suite_notice:
        line1, line2 = suite_notice
        return line1, line2, DEFAULT_SCROLL_MS, "suite-down"

    if not lock_payload.enabled:
        return "", "", DEFAULT_SCROLL_MS, "disabled"

    line1, line2, speed, _, _, _ = lock_payload
    return line1, line2, speed, "lock-file"


def _lcd_clock_enabled() -> bool:
    return not os.getenv("DISABLE_LCD_CLOCK")


def _clock_payload(now: datetime) -> tuple[str, str, int, str]:
    return (
        f"Date {now.strftime(CLOCK_DATE_FORMAT)}",
        f"Time {now.strftime(CLOCK_TIME_FORMAT)}",
        DEFAULT_SCROLL_MS,
        "clock",
    )


_DJANGO_READY = False
_SHUTDOWN_REQUESTED = False


def _ensure_django() -> bool:
    global _DJANGO_READY
    if _DJANGO_READY:
        return True

    try:
        import django
    except Exception:
        logger.debug("Django import failed for Net Message broadcast", exc_info=True)
        return False

    try:
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
        django.setup()
    except Exception:
        logger.debug("Django setup failed for Net Message broadcast", exc_info=True)
        return False

    _DJANGO_READY = True
    return True


def _request_shutdown(signum, frame) -> None:  # pragma: no cover - signal handler
    """Mark the loop for shutdown when the process receives a signal."""

    global _SHUTDOWN_REQUESTED
    _SHUTDOWN_REQUESTED = True


def _shutdown_requested() -> bool:
    return _SHUTDOWN_REQUESTED


def _reset_shutdown_flag() -> None:
    global _SHUTDOWN_REQUESTED
    _SHUTDOWN_REQUESTED = False


def _blank_display(lcd: CharLCD1602 | None) -> None:
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


def _handle_shutdown_request(lcd: CharLCD1602 | None) -> bool:
    """Blank the display and signal the loop to exit when shutting down."""

    if not _shutdown_requested():
        return False

    _blank_display(lcd)
    return True


def _broadcast_net_message(subject: str, body: str) -> bool:
    if not _ensure_django():
        return False

    try:
        from apps.nodes.models import NetMessage
    except Exception:
        logger.debug("Net Message model unavailable", exc_info=True)
        return False

    try:
        NetMessage.broadcast(subject=subject, body=body)
        return True
    except Exception:
        logger.warning("Failed to broadcast Net Message", exc_info=True)
        return False


def _send_net_message_from_lock(
    lock_payload: LockPayload, last_sent: tuple[str, str] | None
) -> tuple[str, str] | None:
    if not lock_payload.net_message:
        return last_sent

    current = (lock_payload.line1, lock_payload.line2)
    if last_sent == current:
        return last_sent

    if _broadcast_net_message(*current):
        return current

    return last_sent


def _display(lcd: CharLCD1602, line1: str, line2: str, scroll_ms: int) -> None:
    state = _prepare_display_state(line1, line2, scroll_ms)
    _advance_display(lcd, state)


def _prepare_display_state(line1: str, line2: str, scroll_ms: int) -> DisplayState:
    scroll_sec = max(scroll_ms, 0) / 1000.0
    text1 = line1[:64]
    text2 = line2[:64]
    pad1 = (
        text1 + " " * SCROLL_PADDING
        if len(text1) > LCD_COLUMNS
        else text1.ljust(LCD_COLUMNS)
    )
    pad2 = (
        text2 + " " * SCROLL_PADDING
        if len(text2) > LCD_COLUMNS
        else text2.ljust(LCD_COLUMNS)
    )
    steps1 = max(len(pad1) - (LCD_COLUMNS - 1), 1)
    steps2 = max(len(pad2) - (LCD_COLUMNS - 1), 1)
    cycle = math.lcm(steps1, steps2)
    return DisplayState(pad1, pad2, steps1, steps2, 0, 0, scroll_sec, cycle)


def _advance_display(lcd: CharLCD1602, state: DisplayState) -> DisplayState:
    if _shutdown_requested():
        return state

    segment1 = state.pad1[state.index1 : state.index1 + LCD_COLUMNS]
    segment2 = state.pad2[state.index2 : state.index2 + LCD_COLUMNS]
    lcd.write(0, 0, segment1.ljust(LCD_COLUMNS))
    lcd.write(0, 1, segment2.ljust(LCD_COLUMNS))

    next_index1 = (state.index1 + 1) % state.steps1
    next_index2 = (state.index2 + 1) % state.steps2
    return state._replace(index1=next_index1, index2=next_index2)


def main() -> None:  # pragma: no cover - hardware dependent
    lcd = None
    last_lock_mtime = 0.0
    lock_payload: LockPayload = LockPayload(
        "", "", DEFAULT_SCROLL_MS, False, False, False
    )
    last_display: tuple[str, str, int, str] | None = None
    display_state: DisplayState | None = None
    last_net_message: tuple[str, str] | None = None
    last_clock_minute: tuple[int, int, int, int, int] | None = None
    clock_until = 0.0

    signal.signal(signal.SIGTERM, _request_shutdown)
    signal.signal(signal.SIGINT, _request_shutdown)
    signal.signal(signal.SIGHUP, _request_shutdown)

    try:
        while True:
            sleep_duration = 0.5

            if _handle_shutdown_request(lcd):
                break

            try:
                if LOCK_FILE.exists():
                    mtime = LOCK_FILE.stat().st_mtime
                    if mtime != last_lock_mtime:
                        lock_payload = _read_lock_file()
                        last_lock_mtime = mtime
                else:
                    last_lock_mtime = 0.0

                last_net_message = _send_net_message_from_lock(
                    lock_payload, last_net_message
                )

                now = datetime.now()
                if _lcd_clock_enabled():
                    current_minute = (
                        now.year,
                        now.month,
                        now.day,
                        now.hour,
                        now.minute,
                    )
                    if clock_until <= now.timestamp() and current_minute != last_clock_minute:
                        clock_until = now.timestamp() + CLOCK_MESSAGE_SECONDS
                        last_clock_minute = current_minute

                if clock_until > now.timestamp():
                    line1, line2, speed, source = _clock_payload(now)
                else:
                    line1, line2, speed, source = _resolve_display_payload(lock_payload)
                current_display = (line1, line2, speed, source)
                if current_display != last_display or lcd is None:
                    if lcd is None:
                        lcd = CharLCD1602()
                        lcd.init_lcd()
                    lcd.clear()
                    display_state = _prepare_display_state(line1, line2, speed)
                    last_display = current_display
                    if (
                        source == "lock-file"
                        and not lock_payload.sticky
                        and _lock_file_matches(lock_payload, last_lock_mtime)
                    ):
                        _clear_lock_file()

                if lcd and display_state:
                    display_state = _advance_display(lcd, display_state)
                    sleep_duration = display_state.scroll_sec or sleep_duration
            except LCDUnavailableError as exc:
                logger.warning("LCD unavailable: %s", exc)
                lcd = None
                display_state = None
            except Exception as exc:
                should_disable = _handle_lcd_failure(exc)
                lcd = None
                display_state = None
                if should_disable:
                    break
            time.sleep(sleep_duration)
    finally:
        _blank_display(lcd)
        _reset_shutdown_flag()


if __name__ == "__main__":  # pragma: no cover - script entry point
    main()
