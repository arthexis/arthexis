from __future__ import annotations

from datetime import datetime, timedelta, timezone as datetime_timezone


def _parse_expiry_text(raw: str, *, now: datetime) -> datetime | None:
    text = raw.strip()
    if not text:
        return None

    if text.isdigit():
        return now + timedelta(seconds=int(text))

    if text[-1] in {"Z", "z"}:
        text = f"{text[:-1]}+00:00"

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime_timezone.utc)

    return parsed.astimezone(datetime_timezone.utc)


def parse_event_expiry(
    value: object | None,
    *,
    now: datetime,
    default_seconds: int,
) -> datetime:
    if value is None:
        return now + timedelta(seconds=default_seconds)

    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = _parse_expiry_text(str(value), now=now)
        if parsed is None:
            return now + timedelta(seconds=default_seconds)

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime_timezone.utc)

    return parsed.astimezone(datetime_timezone.utc)


def parse_event_expiry_candidate(raw: str, *, now: datetime) -> datetime | None:
    return _parse_expiry_text(raw, now=now)
