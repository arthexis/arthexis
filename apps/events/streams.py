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
EVENT_STREAM_MAXLEN = 1000


def mask_identifier(value: str | None, *, visible: int = 4) -> str:
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= visible:
        return "*" * len(text)
    return "*" * (len(text) - visible) + text[-visible:]


def _redis_url() -> str:
    return str(
        getattr(settings, "EVENTS_REDIS_URL", "")
        or getattr(settings, "OCPP_STATE_REDIS_URL", "")
        or ""
    ).strip()


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
    try:
        return _redis_client(url).xadd(
            EVENT_STREAM,
            event,
            maxlen=EVENT_STREAM_MAXLEN,
            approximate=True,
        )
    except RedisError:
        logger.exception("events.redis_publish_failed", extra={"event_type": event_type})
        return None


async def aemit_event(event_type: str, /, **fields: Any) -> str | None:
    """Asynchronously append an event without blocking an ASGI handler."""

    return await sync_to_async(emit_event, thread_sensitive=False)(event_type, **fields)
