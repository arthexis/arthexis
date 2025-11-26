from __future__ import annotations

import logging
import os
import socket
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


def _maybe_setup_django() -> bool:
    try:
        import django
    except Exception:
        return False

    try:
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
        django.setup()
        return True
    except Exception:
        logger.debug("Django setup failed for startup notification", exc_info=True)
        return False


def _should_mark_nonrelease(version: str, current_revision: str) -> bool:
    if not version or not current_revision:
        return False

    if not _maybe_setup_django():
        return False

    try:
        from core.models import PackageRelease
    except Exception:
        return False

    try:
        normalized = version.lstrip("vV") or version
        base_version = normalized.rstrip("+")
        return not PackageRelease.matches_revision(base_version, current_revision)
    except Exception:
        logger.debug("Startup release comparison failed", exc_info=True)
        return False


def build_startup_message(
    base_dir: Path, port: str | None = None, *, allow_db_lookup: bool = True
) -> tuple[str, str]:
    host = socket.gethostname()
    port_value = port or os.environ.get("PORT", "8888")

    version = ""
    ver_path = Path(base_dir) / "VERSION"
    if ver_path.exists():
        try:
            version = ver_path.read_text().strip()
        except Exception:
            logger.debug("Failed to read VERSION file", exc_info=True)

    revision_value = revision.get_revision()
    rev_short = revision_value[-6:] if revision_value else ""

    body = version
    if body:
        normalized = body.lstrip("vV") or body
        needs_marker = allow_db_lookup and _should_mark_nonrelease(
            normalized, revision_value
        )
        if needs_marker and not normalized.endswith("+"):
            body = f"{body}+"
    if rev_short:
        body = f"{body} r{rev_short}" if body else f"r{rev_short}"

    subject = f"{host}:{port_value}"
    return subject, body


def render_lcd_payload(
    subject: str,
    body: str,
    *,
    net_message: bool = False,
    scroll_ms: int | None = None,
) -> str:
    lines: list[str] = [subject[:64], body[:64]]
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
    allow_db_lookup: bool = True,
) -> Path:
    subject, body = build_startup_message(
        base_dir=base_dir, port=port, allow_db_lookup=allow_db_lookup
    )
    payload = render_lcd_payload(subject, body, net_message=True)

    target = lock_file or (Path(base_dir) / "locks" / "lcd_screen.lck")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(payload, encoding="utf-8")
    return target
