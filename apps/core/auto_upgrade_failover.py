from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from django.utils import timezone
from django.utils.dateparse import parse_datetime


logger = logging.getLogger(__name__)

LOCK_FILENAME = "auto_upgrade_failover.lck"


@dataclass(slots=True)
class FailoverStatus:
    """Structured information about the current failover lock."""

    detail: str
    reason: str
    revision: str
    created: datetime | None


def failover_lock_path(base_dir: Path) -> Path:
    """Return the path to the failover lockfile within ``base_dir``."""

    return base_dir / "locks" / LOCK_FILENAME


def write_failover_lock(
    base_dir: Path,
    *,
    reason: str,
    detail: str | None = None,
    revision: str | None = None,
) -> None:
    """Persist failover lock metadata, ignoring filesystem errors."""

    payload: dict[str, Any] = {
        "reason": reason.strip(),
        "detail": (detail or reason).strip(),
        "created": timezone.now().isoformat(),
    }
    if revision:
        cleaned = revision.strip()
        if cleaned:
            payload["revision"] = cleaned

    lock_path = failover_lock_path(base_dir)
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError:
        logger.warning("Failed to update auto-upgrade failover lockfile")


def clear_failover_lock(base_dir: Path) -> None:
    """Remove the failover lock when present."""

    lock_path = failover_lock_path(base_dir)
    try:
        lock_path.unlink()
    except FileNotFoundError:
        return
    except OSError:
        logger.warning("Failed to clear auto-upgrade failover lockfile")


def read_failover_status(base_dir: Path) -> FailoverStatus | None:
    """Return the stored failover status when the lock exists."""

    lock_path = failover_lock_path(base_dir)
    try:
        raw_value = lock_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        logger.warning("Failed to read auto-upgrade failover lockfile")
        return None

    try:
        data = json.loads(raw_value)
    except json.JSONDecodeError:
        logger.warning("Invalid auto-upgrade failover lockfile contents: %s", raw_value)
        return None

    reason = str(data.get("reason", "")).strip()
    detail = str(data.get("detail", "")).strip() or reason
    revision = str(data.get("revision", "")).strip()

    created_value = data.get("created")
    created: datetime | None = None
    if isinstance(created_value, str):
        parsed = parse_datetime(created_value.strip())
        if parsed is not None:
            if timezone.is_naive(parsed):
                parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
            created = parsed

    if not reason and detail:
        reason = detail
    elif not detail and reason:
        detail = reason

    return FailoverStatus(
        detail=detail,
        reason=reason,
        revision=revision,
        created=created,
    )
