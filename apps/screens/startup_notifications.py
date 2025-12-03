from __future__ import annotations

import logging
import os
import socket
from collections.abc import Iterable
from pathlib import Path

from utils import revision

logger = logging.getLogger(__name__)

STARTUP_NET_MESSAGE_FLAG = "net-message"
LCD_FEATURE_LOCK = "lcd_screen_enabled.lck"
LCD_RUNTIME_LOCK = "lcd_screen.lck"


def lcd_feature_enabled(lock_dir: Path) -> bool:
    """Return True when the LCD feature flag or runtime lock is present."""

    if not lock_dir:
        return False

    feature_lock = lock_dir / LCD_FEATURE_LOCK
    runtime_lock = lock_dir / LCD_RUNTIME_LOCK
    return feature_lock.exists() or runtime_lock.exists()


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
    lines: list[str] = [subject.strip()[:64], body.strip()[:64]]
    if net_message:
        lines.append(STARTUP_NET_MESSAGE_FLAG)
    if scroll_ms is not None:
        lines.append(str(scroll_ms))
    return "\n".join(lines) + "\n"


def queue_startup_message(
    *,
    base_dir: Path,
    port: str | None = None,
    lock_file: Path | None = None,
) -> Path:
    subject, body = build_startup_message(base_dir=base_dir, port=port)
    payload = render_lcd_payload(subject, body, net_message=True)

    target = lock_file or (Path(base_dir) / ".locks" / "lcd_screen.lck")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(payload, encoding="utf-8")
    return target
