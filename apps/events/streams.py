from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Any

from asgiref.sync import sync_to_async
from django.conf import settings
from django.utils import timezone
from redis import Redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

EVENT_STREAM = "arthexis:events"
DEFAULT_EVENT_STREAM_MAXLEN = 100_000


def _redis_url() -> str:
    return str(
        getattr(settings, "EVENTS_REDIS_URL", "")
        or getattr(settings, "OCPP_STATE_REDIS_URL", "")
        or ""
    ).strip()


def _stream_maxlen() -> int:
    value = getattr(settings, "EVENTS_STREAM_MAXLEN", DEFAULT_EVENT_STREAM_MAXLEN)
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return DEFAULT_EVENT_STREAM_MAXLEN
    return max(parsed, 0)


@lru_cache(maxsize=4)
def _redis_client(url: str) -> Redis:
    return Redis.from_url(url, decode_responses=True)


def _redis_value(value: Any) -> str:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def emit_event(event_type: str, /, **fields: Any) -> str | None:
    """Append a typed event to the local Redis stream without failing callers."""

    url = _redis_url()
    if not url:
        return None

    event = {
        "type": event_type,
        "timestamp": timezone.now().isoformat(),
        **{
            key: _redis_value(value)
            for key, value in fields.items()
            if value is not None
        },
    }
    maxlen = _stream_maxlen()
    kwargs: dict[str, object] = {}
    if maxlen:
        kwargs = {"maxlen": maxlen, "approximate": True}
    try:
        return _redis_client(url).xadd(EVENT_STREAM, event, **kwargs)
    except RedisError:
        logger.exception(
            "events.redis_publish_failed", extra={"event_type": event_type}
        )
        return None


async def aemit_event(event_type: str, /, **fields: Any) -> str | None:
    """Asynchronously append an event without blocking an ASGI handler."""

    return await sync_to_async(emit_event, thread_sensitive=False)(event_type, **fields)
