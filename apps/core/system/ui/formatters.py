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


def format_datetime(dt: datetime | None) -> str:
    """Return *dt* formatted for UI output."""

    if not dt:
        return ""
    return date_format(timezone.localtime(dt), "Y-m-d H:i")


_format_datetime = format_datetime
