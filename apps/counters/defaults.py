from __future__ import annotations

from typing import Any

from django.contrib.contenttypes.models import ContentType

from .models import BadgeCounter

DEFAULT_BADGE_COUNTERS: tuple[dict[str, Any], ...] = (
    {
        "app_label": "nodes",
        "model": "node",
        "name": "Nodes",
        "primary_source": "apps.counters.badge_values.node_known_count",
    },
    {
        "app_label": "cards",
        "model": "rfid",
        "name": "RFIDs",
        "primary_source": "apps.counters.badge_values.rfid_release_stats",
    },
    {
        "app_label": "counters",
        "model": "badgecounter",
        "name": "Badge Counters",
        "primary_source": "apps.counters.badge_values.badge_counter_count",
    },
)


def ensure_default_badge_counters() -> list[BadgeCounter]:
    """Create or refresh built-in badge counters when dependencies exist."""

    created_or_updated: list[BadgeCounter] = []

    for config in DEFAULT_BADGE_COUNTERS:
        content_type = ContentType.objects.filter(
            app_label=config["app_label"], model=config["model"]
        ).first()
        if content_type is None:
            continue

        defaults = {
            "priority": 0,
            "primary_source_type": BadgeCounter.ValueSource.CALLABLE,
            "primary_source": config["primary_source"],
            "secondary_source_type": None,
            "secondary_source": "",
            "label_template": "",
            "separator": "/",
            "css_class": "badge-counter",
            "is_enabled": True,
            "is_seed_data": True,
        }

        badge, created = BadgeCounter.objects.get_or_create(
            content_type=content_type,
            name=config["name"],
            defaults=defaults,
        )

        updates = {
            field: value
            for field, value in defaults.items()
            if getattr(badge, field) != value
        }
        if updates:
            BadgeCounter.objects.filter(pk=badge.pk).update(**updates)
            badge.refresh_from_db()

        if created or updates:
            created_or_updated.append(badge)

    return created_or_updated
