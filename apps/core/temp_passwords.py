"""Utilities for temporary password lock files."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import string
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.utils import timezone
from django.utils.dateparse import parse_datetime


DEFAULT_PASSWORD_LENGTH = 16
DEFAULT_EXPIRATION = timedelta(hours=1)
_SAFE_COMPONENT_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def _base_lock_dir() -> Path:
    """Return the root directory used for temporary password lock files."""

    configured = getattr(settings, "TEMP_PASSWORD_LOCK_DIR", None)
    if configured:
        path = Path(configured)
    else:
        path = Path(settings.BASE_DIR) / "locks" / "temp-passwords"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_component(value: str) -> str:
    """Return a filesystem safe component derived from ``value``."""

    if not value:
        return ""
    safe = _SAFE_COMPONENT_RE.sub("_", value)
    safe = safe.strip("._")
    return safe[:64]


def _lockfile_name(username: str) -> str:
    """Return the filename used for the provided ``username``."""

    digest = hashlib.sha256(username.encode("utf-8")).hexdigest()[:12]
    safe = _safe_component(username)
    if safe:
        return f"{safe}-{digest}.json"
    return f"user-{digest}.json"


def _lockfile_path(username: str) -> Path:
    """Return the lockfile path for ``username``."""

    return _base_lock_dir() / _lockfile_name(username)


def _parse_timestamp(value: str | None) -> Optional[datetime]:
    """Return a timezone aware datetime parsed from ``value``."""

    if not value:
        return None
    parsed = parse_datetime(value)
    if parsed is None:
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)
    return parsed


@dataclass(frozen=True)
class TempPasswordEntry:
    """Details for a temporary password stored on disk."""

    username: str
    password_hash: str
    expires_at: datetime
    created_at: datetime
    path: Path
    allow_change: bool = False

    @property
    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at

    def check_password(self, raw_password: str) -> bool:
        """Return ``True`` if ``raw_password`` matches this entry."""

        return check_password(raw_password, self.password_hash)


def generate_password(length: int = DEFAULT_PASSWORD_LENGTH) -> str:
    """Return a random password composed of letters and digits."""

    if length <= 0:
        raise ValueError("length must be a positive integer")
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def store_temp_password(
    username: str,
    raw_password: str,
    expires_at: Optional[datetime] = None,
    *,
    allow_change: bool = False,
) -> TempPasswordEntry:
    """Persist a temporary password for ``username`` and return the entry."""

    if expires_at is None:
        expires_at = timezone.now() + DEFAULT_EXPIRATION
    if timezone.is_naive(expires_at):
        expires_at = timezone.make_aware(expires_at)
    created_at = timezone.now()
    path = _lockfile_path(username)
    data = {
        "username": username,
        "password_hash": make_password(raw_password),
        "expires_at": expires_at.isoformat(),
        "created_at": created_at.isoformat(),
        "allow_change": allow_change,
    }
    path.write_text(json.dumps(data, indent=2, sort_keys=True))
    return TempPasswordEntry(
        username=username,
        password_hash=data["password_hash"],
        expires_at=expires_at,
        created_at=created_at,
        path=path,
        allow_change=allow_change,
    )


def load_temp_password(username: str) -> Optional[TempPasswordEntry]:
    """Return the stored temporary password for ``username``, if any."""

    path = _lockfile_path(username)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, UnicodeDecodeError):
        path.unlink(missing_ok=True)
        return None

    expires_at = _parse_timestamp(data.get("expires_at"))
    created_at = _parse_timestamp(data.get("created_at")) or timezone.now()
    password_hash = data.get("password_hash")
    if not expires_at or not password_hash:
        path.unlink(missing_ok=True)
        return None

    username = data.get("username") or username
    allow_change_value = data.get("allow_change", False)
    if isinstance(allow_change_value, str):
        allow_change = allow_change_value.lower() in {"1", "true", "yes", "on"}
    else:
        allow_change = bool(allow_change_value)

    return TempPasswordEntry(
        username=username,
        password_hash=password_hash,
        expires_at=expires_at,
        created_at=created_at,
        path=path,
        allow_change=allow_change,
    )


def discard_temp_password(username: str) -> None:
    """Remove any stored temporary password for ``username``."""

    path = _lockfile_path(username)
    path.unlink(missing_ok=True)

