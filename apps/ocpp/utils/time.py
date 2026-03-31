from __future__ import annotations

from datetime import datetime
from typing import TypeAlias

from django.utils import timezone
from django.utils.dateparse import parse_datetime

OcppTimestampInput: TypeAlias = datetime | str | int | None


def _parse_ocpp_timestamp(value: OcppTimestampInput) -> datetime | None:
    """Return an aware :class:`~datetime.datetime` for OCPP timestamps.

    Accepts a :class:`datetime.datetime` object or a string. If the value is
    naive it will be converted to the current timezone. Invalid or empty values
    return ``None``.
    """

    if value in (None, "", 0):
        return None

    timestamp: datetime | None
    if isinstance(value, datetime):
        timestamp = value
    elif isinstance(value, str):
        timestamp = parse_datetime(value)
    else:
        return None
    if not timestamp:
        return None
    if timezone.is_naive(timestamp):
        timestamp = timezone.make_aware(timestamp, timezone.get_current_timezone())
    return timestamp


__all__ = ["OcppTimestampInput", "_parse_ocpp_timestamp"]
