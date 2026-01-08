from __future__ import annotations

import datetime
import logging
import threading
from collections import defaultdict

from django.conf import settings
from django.db import transaction
from django.db.models import Count, QuerySet
from django.db.models.functions import TruncDay, TruncWeek
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from django.utils import timezone

from .models import UsageEvent

logger = logging.getLogger(__name__)
_state = threading.local()


def usage_analytics_enabled() -> bool:
    return bool(getattr(settings, "ENABLE_USAGE_ANALYTICS", False))


def _derive_app_label_from_model(model_label: str) -> str:
    if not model_label:
        return ""
    parts = model_label.split(".")
    if parts and parts[0] == "apps" and len(parts) > 1:
        return parts[1]
    return parts[0]


def _safe_create_event(**kwargs) -> None:
    try:
        UsageEvent.objects.create(**kwargs)
    except Exception:  # pragma: no cover - best effort logging
        logger.debug("Failed to create UsageEvent", exc_info=True)


def record_request_event(
    *,
    timestamp=None,
    user=None,
    app_label: str,
    view_name: str,
    path: str,
    method: str,
    status_code: int,
    action: str = UsageEvent.Action.READ,
    metadata: dict | None = None,
    model_label: str = "",
) -> None:
    if not usage_analytics_enabled():
        return

    _safe_create_event(
        timestamp=timestamp or timezone.now(),
        user=user if getattr(user, "is_authenticated", False) else None,
        app_label=app_label or "",
        view_name=view_name or "",
        path=path or "",
        method=method or "",
        status_code=status_code,
        action=action,
        model_label=model_label or "",
        metadata=metadata or {},
    )


def _get_buffer():
    buffer = getattr(_state, "buffer", None)
    if buffer is None:
        buffer = defaultdict(lambda: {"count": 0, "metadata": {}})
        _state.buffer = buffer
        _state.flush_registered = False
    return buffer


def _reset_buffer():
    _state.buffer = defaultdict(lambda: {"count": 0, "metadata": {}})
    _state.flush_registered = False


def record_model_event(
    *,
    model_label: str,
    action: str,
    metadata: dict | None = None,
) -> None:
    if not usage_analytics_enabled():
        return

    buffer = _get_buffer()
    key = (model_label, action)
    entry = buffer[key]
    entry["count"] += 1
    if metadata:
        entry["metadata"].update(metadata)

    connection = transaction.get_connection()
    if connection.in_atomic_block:
        if not getattr(_state, "flush_registered", False):
            transaction.on_commit(_flush_buffer)
            _state.flush_registered = True
    else:
        _flush_buffer()


def _flush_buffer():
    buffer = getattr(_state, "buffer", None)
    if not buffer:
        return

    try:
        for (model_label, action), payload in buffer.items():
            metadata = dict(payload.get("metadata") or {})
            metadata["count"] = payload.get("count", 0)
            app_label = _derive_app_label_from_model(model_label)
            _safe_create_event(
                timestamp=timezone.now(),
                user=None,
                app_label=app_label,
                view_name=model_label or "model-signal",
                path=model_label,
                method="SIGNAL",
                status_code=0,
                action=action,
                model_label=model_label,
                metadata=metadata,
            )
    finally:
        _reset_buffer()


@receiver(post_save, dispatch_uid="core_usage_analytics_post_save", weak=False)
def _usage_post_save(sender, instance, created, **kwargs):
    if sender is UsageEvent:
        return
    if getattr(sender._meta, "auto_created", False):
        return
    if getattr(sender._meta, "abstract", False):
        return
    if kwargs.get("raw"):
        return

    action = UsageEvent.Action.CREATE if created else UsageEvent.Action.UPDATE
    record_model_event(model_label=instance._meta.label_lower, action=action)


@receiver(post_delete, dispatch_uid="core_usage_analytics_post_delete", weak=False)
def _usage_post_delete(sender, instance, **kwargs):
    if sender is UsageEvent:
        return
    if getattr(sender._meta, "auto_created", False):
        return
    if getattr(sender._meta, "abstract", False):
        return
    if kwargs.get("raw"):
        return

    record_model_event(model_label=instance._meta.label_lower, action=UsageEvent.Action.DELETE)


def build_usage_summary(days: int = 30, queryset: QuerySet[UsageEvent] | None = None) -> dict:
    qs = queryset if queryset is not None else UsageEvent.objects.all()
    start = timezone.now() - datetime.timedelta(days=max(days, 1))
    qs = qs.filter(timestamp__gte=start)

    daily_counts = (
        qs.annotate(day=TruncDay("timestamp"))
        .values("day")
        .annotate(count=Count("id"))
        .order_by("day")
    )
    weekly_counts = (
        qs.annotate(week=TruncWeek("timestamp"))
        .values("week")
        .annotate(count=Count("id"))
        .order_by("week")
    )
    top_apps = (
        qs.values("app_label")
        .annotate(count=Count("id"))
        .order_by("-count", "app_label")[:20]
    )
    top_views = (
        qs.values("app_label", "view_name")
        .annotate(count=Count("id"))
        .order_by("-count", "app_label", "view_name")[:20]
    )
    top_models = (
        qs.exclude(model_label="")
        .values("model_label", "action")
        .annotate(count=Count("id"))
        .order_by("-count", "model_label", "action")[:20]
    )

    def _serialize(rows, key_map):
        return [
            {output_key: row[source_key] for output_key, source_key in key_map.items()}
            for row in rows
        ]

    return {
        "range_start": start.isoformat(),
        "daily_counts": _serialize(daily_counts, {"day": "day", "count": "count"}),
        "weekly_counts": _serialize(weekly_counts, {"week": "week", "count": "count"}),
        "top_apps": _serialize(top_apps, {"app_label": "app_label", "count": "count"}),
        "top_views": _serialize(
            top_views,
            {"app_label": "app_label", "view_name": "view_name", "count": "count"},
        ),
        "top_models": _serialize(
            top_models,
            {"model_label": "model_label", "action": "action", "count": "count"},
        ),
    }

