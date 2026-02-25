"""Formatting helpers for system UI output.

The helpers in this module accept timezone-aware ``datetime`` objects when
available and return presentation-ready strings for admin templates and
command output. Naive timestamps are tolerated and passed through as-is if
localization cannot be applied.
"""

from __future__ import annotations

from datetime import datetime

from django.utils import timezone
from django.utils.formats import date_format


def _format_timestamp(dt: datetime | None) -> str:
    """Return ``dt`` formatted using the active ``DATETIME_FORMAT``."""

    if dt is None:
        return ""
    try:
        localized = timezone.localtime(dt)
    except Exception:
        localized = dt
    return date_format(localized, "DATETIME_FORMAT")


def _format_datetime(dt: datetime | None) -> str:
    """Return ``dt`` formatted as ``YYYY-mm-dd HH:MM`` for concise UI labels."""

    if dt is None:
        return ""
    if timezone.is_aware(dt):
        dt = timezone.localtime(dt)
    return date_format(dt, "Y-m-d H:i")


def format_datetime(dt: datetime | None) -> str:
    """Return *dt* formatted for UI output."""

    return _format_datetime(dt)
