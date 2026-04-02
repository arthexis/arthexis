from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone as datetime_timezone
from pathlib import Path

logger = logging.getLogger(__name__)

LCD_HIGH_LOCK_FILE = "lcd-high"
LCD_LOW_LOCK_FILE = "lcd-low"
LCD_CLOCK_LOCK_FILE = "clock"
LCD_UPTIME_LOCK_FILE = "uptime"
LCD_STATS_LOCK_FILE = "stats"
LCD_CHANNELS_LOCK_FILE = "lcd-channels.lck"
LCD_LEGACY_FEATURE_LOCK = "lcd_screen_enabled.lck"
LCD_RUNTIME_LOCK_FILE = "lcd_screen.lck"
LCD_COLUMNS = 16


@dataclass(frozen=True)
class LcdMessage:
    subject: str
    body: str
    expires_at: datetime | None = None


def _parse_expires_at(value: str | None) -> datetime | None:
    if not value:
        return None

    text = value.strip()
    if not text:
        return None

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime_timezone.utc)

    return parsed


def _parse_lcd_lock_lines(lines: list[str]) -> LcdMessage:
    subject = lines[0][:64] if lines else ""
    body = lines[1][:64] if len(lines) > 1 else ""
    expires_at = _parse_expires_at(lines[2]) if len(lines) > 2 else None
    return LcdMessage(subject=subject, body=body, expires_at=expires_at)


def read_lcd_lock_file(lock_file: Path) -> LcdMessage | None:
    try:
        lines = lock_file.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return None
    except OSError:
        logger.debug("Failed to read LCD lock file: %s", lock_file, exc_info=True)
        return None
    return _parse_lcd_lock_lines(lines)


def _format_expires_at(value: datetime | str | None) -> str:
    if not value:
        return ""

    if isinstance(value, datetime):
        expires_at = value
    else:
        try:
            expires_at = datetime.fromisoformat(str(value))
        except ValueError:
            return ""

    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=datetime_timezone.utc)

    return expires_at.isoformat()


def render_lcd_lock_file(*, subject: str = "", body: str = "", expires_at=None) -> str:
    lines = [subject.strip()[:64], body.strip()[:64]]
    expires_line = _format_expires_at(expires_at)
    if expires_line:
        lines.append(expires_line)
    return "\n".join(lines) + "\n"


def format_lcd_line(text: str, *, width: int = LCD_COLUMNS) -> str:
    normalized = "".join(ch if 32 <= ord(ch) < 127 else " " for ch in (text or ""))
    normalized = normalized.strip()
    if len(normalized) <= width:
        return normalized.ljust(width)
    trimmed = normalized[: width - 3].rstrip()
    return f"{trimmed}...".ljust(width)


def format_lcd_lines(subject: str, body: str, *, width: int = LCD_COLUMNS) -> tuple[str, str]:
    return format_lcd_line(subject, width=width), format_lcd_line(body, width=width)


def ensure_lock_dir(lock_dir: Path) -> Path | None:
    if not lock_dir:
        return None
    lock_dir.mkdir(parents=True, exist_ok=True)
    return lock_dir


def lcd_feature_enabled(lock_dir: Path) -> bool:
    """Return True when the LCD feature flag or runtime lock is present."""

    if not lock_dir:
        return False

    for name in (
        LCD_HIGH_LOCK_FILE,
        LCD_LOW_LOCK_FILE,
        LCD_CLOCK_LOCK_FILE,
        LCD_UPTIME_LOCK_FILE,
        LCD_STATS_LOCK_FILE,
        LCD_CHANNELS_LOCK_FILE,
        LCD_LEGACY_FEATURE_LOCK,
        LCD_RUNTIME_LOCK_FILE,
    ):
        if (lock_dir / name).exists():
            return True
    return False


def lcd_feature_enabled_in_dirs(lock_dirs: Iterable[Path] | None) -> bool:
    """Return True when any provided lock directory enables the LCD feature."""

    if not lock_dirs:
        return False

    for lock_dir in lock_dirs:
        if lcd_feature_enabled(lock_dir):
            return True
    return False


def lcd_feature_enabled_for_paths(base_dir: Path, node_base_path: Path) -> bool:
    """Return True when LCD locks exist in the node or project lock directories."""

    lock_dirs: list[Path] = []
    for candidate in (node_base_path / ".locks", Path(base_dir) / ".locks"):
        try:
            resolved = candidate.resolve()
        except Exception:
            resolved = candidate
        if resolved not in lock_dirs:
            lock_dirs.append(resolved)

    return lcd_feature_enabled_in_dirs(lock_dirs)


def render_lcd_payload(
    subject: str,
    body: str,
) -> str:
    return render_lcd_lock_file(subject=subject, body=body)


def write_lcd_message(
    *,
    lock_file: Path,
    subject: str,
    body: str,
    expires_at: datetime | str | None = None,
) -> Path:
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    payload = render_lcd_lock_file(subject=subject, body=body, expires_at=expires_at)
    lock_file.write_text(payload, encoding="utf-8")
    return lock_file
