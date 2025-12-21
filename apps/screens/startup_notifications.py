from __future__ import annotations

import logging
import os
import socket
from collections.abc import Iterable
from pathlib import Path
from typing import NamedTuple

from utils import revision

logger = logging.getLogger(__name__)

STARTUP_NET_MESSAGE_FLAG = "net-message"
LCD_LOCK_FILE = "lcd_screen.lck"
LCD_LEGACY_FEATURE_LOCK = "lcd_screen_enabled.lck"
LCD_STATE_PREFIX = "state="
LCD_STATE_ENABLED = "enabled"
LCD_STATE_DISABLED = "disabled"


class LcdLockInfo(NamedTuple):
    enabled: bool
    subject: str
    body: str
    net_message: bool
    scroll_ms: int | None
    extra_flags: tuple[str, ...]


def _parse_state_line(line: str) -> tuple[bool, int]:
    if not line:
        return True, 0
    raw = line.strip()
    if not raw.lower().startswith(LCD_STATE_PREFIX):
        return True, 0
    value = raw[len(LCD_STATE_PREFIX) :].strip().lower()
    if value == LCD_STATE_DISABLED:
        return False, 1
    return True, 1


def parse_lcd_lock_lines(lines: list[str]) -> LcdLockInfo:
    enabled, offset = _parse_state_line(lines[0] if lines else "")
    subject = lines[offset].strip()[:64] if len(lines) > offset else ""
    body = lines[offset + 1].strip()[:64] if len(lines) > offset + 1 else ""
    flag_lines = lines[offset + 2 :] if len(lines) > offset + 2 else []

    net_message = False
    scroll_ms: int | None = None
    extra_flags: list[str] = []
    for line in flag_lines:
        value = line.strip()
        if not value:
            continue
        lowered = value.lower()
        if lowered == STARTUP_NET_MESSAGE_FLAG:
            net_message = True
            continue
        if lowered.startswith("scroll_ms="):
            raw_value = lowered.split("=", 1)[1].strip()
            try:
                scroll_ms = int(raw_value)
            except ValueError:
                pass
            continue
        if lowered.isdigit():
            try:
                scroll_ms = int(lowered)
            except ValueError:
                pass
            continue
        extra_flags.append(value)

    return LcdLockInfo(
        enabled=enabled,
        subject=subject,
        body=body,
        net_message=net_message,
        scroll_ms=scroll_ms,
        extra_flags=tuple(extra_flags),
    )


def read_lcd_lock_file(lock_file: Path) -> LcdLockInfo | None:
    try:
        content = lock_file.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        logger.debug("Failed to read LCD lock file", exc_info=True)
        return None
    return parse_lcd_lock_lines(content.splitlines())


def render_lcd_lock_file(
    subject: str,
    body: str,
    *,
    enabled: bool = True,
    net_message: bool = False,
    scroll_ms: int | None = None,
    extra_flags: Iterable[str] | None = None,
) -> str:
    state_value = LCD_STATE_ENABLED if enabled else LCD_STATE_DISABLED
    lines = [
        f"{LCD_STATE_PREFIX}{state_value}",
        subject.strip()[:64],
        body.strip()[:64],
    ]

    flags: list[str] = []
    if net_message:
        flags.append(STARTUP_NET_MESSAGE_FLAG)
    if scroll_ms is not None:
        flags.append(f"scroll_ms={scroll_ms}")
    if extra_flags:
        for flag in extra_flags:
            value = str(flag).strip()
            if not value:
                continue
            lowered = value.lower()
            if lowered == STARTUP_NET_MESSAGE_FLAG or lowered.startswith("scroll_ms="):
                continue
            flags.append(value)

    if flags:
        lines.extend(flags)

    return "\n".join(lines) + "\n"


def ensure_lcd_lock_file(lock_dir: Path) -> Path | None:
    if not lock_dir:
        return None

    lock_file = lock_dir / LCD_LOCK_FILE
    if lock_file.exists():
        return lock_file

    legacy_lock = lock_dir / LCD_LEGACY_FEATURE_LOCK
    if legacy_lock.exists():
        lock_dir.mkdir(parents=True, exist_ok=True)
        payload = render_lcd_lock_file("", "", enabled=True)
        lock_file.write_text(payload, encoding="utf-8")
    return lock_file


def lcd_feature_enabled(lock_dir: Path) -> bool:
    """Return True when the LCD lock file exists and is enabled."""

    if not lock_dir:
        return False

    lock_file = ensure_lcd_lock_file(lock_dir)
    if lock_file is None or not lock_file.exists():
        return False
    info = read_lcd_lock_file(lock_file)
    return bool(info and info.enabled)


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


def build_startup_message(base_dir: Path, port: str | None = None) -> tuple[str, str]:
    host = (socket.gethostname() or "").strip()
    port_value = (port if port is not None else os.environ.get("PORT", "8888")).strip()
    if not port_value:
        port_value = "8888"

    version = ""
    ver_path = Path(base_dir) / "VERSION"
    if ver_path.exists():
        try:
            version = ver_path.read_text().strip()
        except Exception:
            logger.debug("Failed to read VERSION file", exc_info=True)

    revision_value = (revision.get_revision() or "").strip()
    rev_short = revision_value[-6:] if revision_value else ""

    body_parts = []
    if version:
        body_parts.append(version)
    if rev_short:
        body_parts.append(rev_short)

    body = " ".join(body_parts)

    subject = f"{host}:{port_value}".strip()
    return subject, body.strip()


def queue_startup_message(
    *,
    base_dir: Path,
    port: str | None = None,
    lock_file: Path | None = None,
) -> Path:
    subject, body = build_startup_message(base_dir=base_dir, port=port)

    target = lock_file or (Path(base_dir) / ".locks" / LCD_LOCK_FILE)
    target.parent.mkdir(parents=True, exist_ok=True)
    existing_info = read_lcd_lock_file(target)
    if existing_info is None:
        legacy_lock = target.parent / LCD_LEGACY_FEATURE_LOCK
        if legacy_lock.exists():
            ensure_lcd_lock_file(target.parent)
            existing_info = read_lcd_lock_file(target)

    enabled = existing_info.enabled if existing_info else True
    scroll_ms = existing_info.scroll_ms if existing_info else None
    extra_flags = existing_info.extra_flags if existing_info else ()
    payload = render_lcd_lock_file(
        subject,
        body,
        enabled=enabled,
        net_message=True,
        scroll_ms=scroll_ms,
        extra_flags=extra_flags,
    )

    target.write_text(payload, encoding="utf-8")
    return target
