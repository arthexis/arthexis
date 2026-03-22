"""Parsing helpers for Evergo model payload normalization."""

from __future__ import annotations

from datetime import datetime
import hashlib
from typing import Any

from django.utils import timezone
from django.utils.dateparse import parse_datetime


def to_int(value: Any) -> int | None:
    """Convert loosely typed API integers into local integer fields."""
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_dt(value: Any) -> datetime | None:
    """Parse ISO datetimes produced by the Evergo API."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    dt = parse_datetime(value)
    if dt is None:
        return None
    if timezone.is_naive(dt):
        return timezone.make_aware(dt)
    return dt


def nested_dict(value: Any, key: str) -> dict[str, Any]:
    """Safely return a dictionary from a nested object lookup."""
    if not isinstance(value, dict):
        return {}
    nested = value.get(key)
    if not isinstance(nested, dict):
        return {}
    return nested


def nested_int(value: Any, key: str) -> int | None:
    """Safely coerce a nested dictionary integer field."""
    if not isinstance(value, dict):
        return None
    return to_int(value.get(key))


def nested_name(value: Any) -> str:
    """Extract a user-facing name from dictionary payloads."""
    if not isinstance(value, dict):
        return ""
    return str(value.get("nombre") or value.get("name") or "")


def first_dict(value: Any) -> dict[str, Any]:
    """Return the first dictionary in a list-like payload field."""
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return value[0]
    return {}


def placeholder_remote_id(*, order_number: str) -> int:
    """Build a deterministic positive integer to persist a provisional SO row."""
    # Reserve [1_500_000_000, 2_000_000_000) for placeholders to avoid collisions
    # with current real Evergo IDs while keeping deterministic order-number mapping.
    digest = hashlib.sha256(order_number.strip().upper().encode("utf-8")).hexdigest()[:12]
    return 1_500_000_000 + (int(digest, 16) % 500_000_000)

