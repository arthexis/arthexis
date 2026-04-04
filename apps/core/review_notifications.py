"""Review-ready notifications for local coding tasks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as datetime_timezone
from pathlib import Path
import subprocess

from django.conf import settings

from apps.core.notifications import notify
from apps.screens.startup_notifications import lcd_feature_enabled

LCD_LINE_WIDTH = 16
DEFAULT_REVIEW_NOTIFICATION_EXPIRY_SECONDS = 1800


@dataclass(frozen=True)
class ReviewNotificationResult:
    """Describe the review notification that was sent or skipped."""

    subject: str
    body: str
    branch: str
    changed_file_count: int | None
    used_lcd: bool
    skipped: bool = False


def _resolve_base_dir(base_dir: Path | str | None = None) -> Path:
    if base_dir is not None:
        return Path(base_dir)
    return Path(settings.BASE_DIR)


def _run_git(base_dir: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=base_dir,
            capture_output=True,
            check=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    return result.stdout


def _clip_lcd_text(text: str, *, width: int = LCD_LINE_WIDTH) -> str:
    normalized = " ".join((text or "").split())
    if len(normalized) <= width:
        return normalized
    if width <= 3:
        return normalized[:width]
    return f"{normalized[: width - 3].rstrip()}..."


def _current_branch(base_dir: Path) -> str:
    output = _run_git(base_dir, "branch", "--show-current")
    return (output or "").strip()


def _changed_file_count(base_dir: Path) -> int | None:
    output = _run_git(base_dir, "status", "--porcelain", "--untracked-files=all")
    if output is None:
        return None
    return sum(1 for line in output.splitlines() if line.strip())


def _default_subject(actor: str) -> str:
    text = (actor or "").strip()
    if not text:
        return "Review ready"
    return _clip_lcd_text(f"{text} ready")


def _default_body(changed_file_count: int | None) -> str:
    if changed_file_count is None:
        return "Review changes"
    noun = "file" if changed_file_count == 1 else "files"
    return _clip_lcd_text(f"{changed_file_count} {noun} changed")


def send_review_notification(
    *,
    actor: str = "Codex",
    summary: str | None = None,
    sticky: bool = True,
    expires_in: int = DEFAULT_REVIEW_NOTIFICATION_EXPIRY_SECONDS,
    force: bool = False,
    base_dir: Path | str | None = None,
) -> ReviewNotificationResult:
    """Send a review-ready notification through the LCD/log fallback stack."""

    resolved_base_dir = _resolve_base_dir(base_dir)
    lock_dir = resolved_base_dir / ".locks"
    used_lcd = lcd_feature_enabled(lock_dir)
    branch = _current_branch(resolved_base_dir)
    changed_file_count = _changed_file_count(resolved_base_dir)
    subject = _default_subject(actor)
    body = _clip_lcd_text(summary) if summary else _default_body(changed_file_count)

    if changed_file_count == 0 and not force:
        return ReviewNotificationResult(
            subject=subject,
            body=body,
            branch=branch,
            changed_file_count=changed_file_count,
            used_lcd=used_lcd,
            skipped=True,
        )

    expires_at = None
    if expires_in > 0:
        expires_at = datetime.now(datetime_timezone.utc) + timedelta(seconds=expires_in)

    # Review-ready notifications should interrupt the LCD immediately when
    # a screen is present instead of waiting for the rotating high channel.
    notify(
        subject=subject,
        body=body,
        sticky=sticky,
        expires_at=expires_at,
        channel_type="event",
    )
    return ReviewNotificationResult(
        subject=subject,
        body=body,
        branch=branch,
        changed_file_count=changed_file_count,
        used_lcd=used_lcd,
    )
