from __future__ import annotations

from datetime import datetime

from django.utils import timezone
from django.utils.dateparse import parse_datetime


def _parse_ocpp_timestamp(value) -> datetime | None:
    """Return an aware :class:`~datetime.datetime` for OCPP timestamps.

    Accepts a :class:`datetime.datetime` object or a string. If the value is
    naive it will be converted to the current timezone. Invalid or empty values
    return ``None``.
    """

    if not value:
        return None
    if isinstance(value, datetime):
        timestamp = value
    else:
        timestamp = parse_datetime(str(value))
    if not timestamp:
        return None
    if timezone.is_naive(timestamp):
        timestamp = timezone.make_aware(timestamp, timezone.get_current_timezone())
    return timestamp
