from datetime import datetime, timezone as dt_timezone

from django.db.models import Count, Q


def annotate_enabled_total(queryset, relation, *, total_alias, enabled_alias):
    return queryset.annotate(
        **{
            total_alias: Count(relation, distinct=True),
            enabled_alias: Count(
                relation,
                filter=Q(**{f"{relation}__is_enabled": True}),
                distinct=True,
            ),
        }
    )


def format_enabled_total(obj, *, enabled_attr, total_attr):
    enabled = getattr(obj, enabled_attr, 0)
    total = getattr(obj, total_attr, 0)
    return f"{enabled}/{total}"


def max_attr(obj, *attrs):
    values = [value for value in (getattr(obj, attr, None) for attr in attrs) if value is not None]
    return max(values) if values else None


def normalize_timestamp(value):
    if value in (None, ""):
        return None

    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return None

    if abs(timestamp) > 100_000_000_000:
        timestamp = timestamp / 1000

    try:
        return datetime.fromtimestamp(timestamp, tz=dt_timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
