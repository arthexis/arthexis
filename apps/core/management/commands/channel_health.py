"""Management command exposing channel-layer and websocket health status."""

from __future__ import annotations

import json
from urllib.parse import urlparse

from django.conf import settings
from django.core.management.base import BaseCommand
from redis import Redis
from redis.exceptions import RedisError

from apps.core.channel_metrics import metrics_snapshot


def _mask_url(url: str) -> str:
    """Mask Redis URL secrets before command output."""

    parsed = urlparse(url)
    if not parsed.password:
        return url
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    user = parsed.username or ""
    auth = f"{user}:****" if user else ":****"
    return parsed._replace(netloc=f"{auth}@{host}{port}").geturl()


class Command(BaseCommand):
    """Display current channel-layer backend and runtime websocket counters."""

    help = "Show channel-layer backend status and in-memory websocket counters"

    def handle(self, *args, **options):  # type: ignore[override]
        decision = getattr(settings, "CHANNEL_LAYER_DECISION", None)
        snapshot = metrics_snapshot()
        payload: dict[str, object] = {
            "backend": getattr(decision, "backend", "unknown"),
            "redis_source": getattr(decision, "redis_source", ""),
            "redis_url": _mask_url(getattr(decision, "redis_url", "")),
            "fallback_reason": getattr(decision, "fallback_reason", ""),
            "metrics": snapshot,
        }

        redis_url = getattr(decision, "redis_url", "")
        if redis_url:
            try:
                client = Redis.from_url(redis_url, decode_responses=True)
                payload["redis_ping"] = bool(client.ping())
            except (RedisError, ValueError, OSError) as exc:
                payload["redis_ping"] = False
                payload["redis_error"] = str(exc)
        else:
            payload["redis_ping"] = None

        self.stdout.write(json.dumps(payload, sort_keys=True, indent=2))
