"""Datetime formatting helpers for system UI payloads.

Data flow: call sites pass ``datetime`` objects (usually timezone-aware values from
Django helpers or parsed startup log lines) and receive localized strings for
rendering in templates and command output.

Expected input formats: parsed log timestamps are Python ``datetime`` instances
created from ISO-8601 strings in startup report records.
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
    """Return ``dt`` in a compact sortable UI format."""

    if not dt:
        return ""
    return date_format(timezone.localtime(dt), "Y-m-d H:i")


def format_datetime(dt: datetime | None) -> str:
    """Return *dt* formatted for UI output."""

    return _format_datetime(dt)
