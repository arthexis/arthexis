from __future__ import annotations

import logging
import os
import socket
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from utils import revision

logger = logging.getLogger(__name__)

STARTUP_NET_MESSAGE_FLAG = "net-message"
LCD_STICKY_NET_MESSAGE_FLAG = "sticky-net-message"
LCD_LOCK_FILE = "lcd_screen.lck"
LCD_LEGACY_FEATURE_LOCK = "lcd_screen_enabled.lck"
LCD_STATE_ENABLED = "enabled"
LCD_STATE_DISABLED = "disabled"
LCD_STATE_PREFIX = "state="


@dataclass(frozen=True)
class LcdLockFile:
    state: str
    subject: str
    body: str
    flags: tuple[str, ...]


def _normalize_lcd_state(raw_state: str | None) -> str:
    normalized = (raw_state or "").strip().lower()
    if normalized == LCD_STATE_DISABLED:
        return LCD_STATE_DISABLED
    return LCD_STATE_ENABLED


def _parse_lcd_lock_lines(lines: list[str]) -> LcdLockFile:
    state = LCD_STATE_ENABLED
    payload_index = 0
    if lines:
        first_line = lines[0].strip()
        if first_line.lower().startswith(LCD_STATE_PREFIX):
            state = _normalize_lcd_state(first_line.split("=", 1)[1])
            payload_index = 1

    subject = lines[payload_index][:64] if len(lines) > payload_index else ""
    body = (
        lines[payload_index + 1][:64]
        if len(lines) > payload_index + 1
        else ""
    )
    flags = tuple(line.strip() for line in lines[payload_index + 2 :] if line.strip())
    return LcdLockFile(state=state, subject=subject, body=body, flags=flags)


def read_lcd_lock_file(lock_file: Path) -> LcdLockFile | None:
    try:
        lines = lock_file.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return None
    except OSError:
        logger.debug("Failed to read LCD lock file: %s", lock_file, exc_info=True)
        return None
    return _parse_lcd_lock_lines(lines)


def render_lcd_lock_file(
    *,
    state: str = LCD_STATE_ENABLED,
    subject: str = "",
    body: str = "",
    flags: Iterable[str] | None = None,
) -> str:
    normalized_state = _normalize_lcd_state(state)
    lines = [
        f"{LCD_STATE_PREFIX}{normalized_state}",
        subject.strip()[:64],
        body.strip()[:64],
    ]
    if flags:
        for flag in flags:
            flag_value = str(flag).strip()
            if flag_value:
                lines.append(flag_value)
    return "\n".join(lines) + "\n"


def parse_lcd_flags(flags: Iterable[str]) -> tuple[bool, int | None, bool]:
    net_message = False
    scroll_ms: int | None = None
    sticky = False
    for flag in flags:
        value = (flag or "").strip()
        if not value:
            continue
        normalized = value.lower()
        if normalized == STARTUP_NET_MESSAGE_FLAG:
            net_message = True
            continue
        if normalized == LCD_STICKY_NET_MESSAGE_FLAG:
            sticky = True
            continue
        if normalized.startswith("scroll_ms="):
            normalized = normalized.split("=", 1)[1].strip()
        try:
            scroll_ms = int(normalized)
        except ValueError:
            continue
    return net_message, scroll_ms, sticky


def ensure_lcd_lock_file(lock_dir: Path) -> Path | None:
    if not lock_dir:
        return None

    lock_file = lock_dir / LCD_LOCK_FILE
    legacy_lock = lock_dir / LCD_LEGACY_FEATURE_LOCK
    if lock_file.exists():
        return lock_file

    if legacy_lock.exists():
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_file.write_text(
            render_lcd_lock_file(state=LCD_STATE_ENABLED), encoding="utf-8"
        )
        return lock_file

    return lock_file


def lcd_feature_enabled(lock_dir: Path) -> bool:
    """Return True when the LCD feature flag or runtime lock is present."""

    if not lock_dir:
        return False

    lock_file = ensure_lcd_lock_file(lock_dir)
    if not lock_file or not lock_file.exists():
        return False

    lock_payload = read_lcd_lock_file(lock_file)
    if lock_payload is None:
        return False
    return lock_payload.state == LCD_STATE_ENABLED


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


def render_lcd_payload(
    subject: str,
    body: str,
    *,
    net_message: bool = False,
    scroll_ms: int | None = None,
) -> str:
    lines: list[str] = [
        LCD_STATE_PREFIX + LCD_STATE_ENABLED,
        subject.strip()[:64],
        body.strip()[:64],
    ]
    if net_message:
        lines.append(STARTUP_NET_MESSAGE_FLAG)
    if scroll_ms is not None:
        lines.append(f"scroll_ms={scroll_ms}")
    return "\n".join(lines) + "\n"


def queue_startup_message(
    *,
    base_dir: Path,
    port: str | None = None,
    lock_file: Path | None = None,
) -> Path:
    subject, body = build_startup_message(base_dir=base_dir, port=port)

    target = lock_file or (Path(base_dir) / ".locks" / LCD_LOCK_FILE)
    target.parent.mkdir(parents=True, exist_ok=True)
    existing = read_lcd_lock_file(target)
    if existing:
        state = existing.state
        flags = [flag for flag in existing.flags if flag != STARTUP_NET_MESSAGE_FLAG]
    else:
        state = LCD_STATE_ENABLED
        flags = []

    flags.append(STARTUP_NET_MESSAGE_FLAG)
    payload = render_lcd_lock_file(
        state=state, subject=subject, body=body, flags=flags
    )
    target.write_text(payload, encoding="utf-8")
    return target
