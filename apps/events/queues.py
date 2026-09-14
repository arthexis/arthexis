from __future__ import annotations

import asyncio
import logging
from typing import Any

from asgiref.sync import sync_to_async
from django.conf import settings
from django.utils import timezone
from kombu import Connection

logger = logging.getLogger(__name__)

DEFAULT_CONNECT_TIMEOUT = 1
DEFAULT_PUBLISH_TIMEOUT = 2


def publish_queue_event(
    queue_name: str,
    event_type: str,
    /,
    **fields: Any,
) -> bool:
    """Publish a plain structured event to a Kombu queue without failing callers."""

    broker_url = str(getattr(settings, "CELERY_BROKER_URL", "")).strip()
    if not broker_url:
        return False

    event = {
        **{
            key: value
            for key, value in fields.items()
            if value is not None and key not in {"type", "timestamp"}
        },
        "type": event_type,
        "timestamp": timezone.now().isoformat(),
    }

    try:
        with Connection(
            broker_url,
            connect_timeout=DEFAULT_CONNECT_TIMEOUT,
            transport_options={
                "socket_timeout": DEFAULT_CONNECT_TIMEOUT,
                "socket_connect_timeout": DEFAULT_CONNECT_TIMEOUT,
            },
        ) as connection:
            connection.ensure_connection(
                max_retries=0,
                timeout=DEFAULT_CONNECT_TIMEOUT,
            )
            with connection.SimpleQueue(queue_name) as queue:
                queue.put(event, serializer="json")
    except Exception:
        logger.exception(
            "events.queue_publish_failed",
            extra={"event_type": event_type, "queue_name": queue_name},
        )
        return False
    return True


async def apublish_queue_event(
    queue_name: str,
    event_type: str,
    /,
    **fields: Any,
) -> bool:
    """Publish an event off the ASGI loop while keeping failures non-fatal."""

    publish = sync_to_async(publish_queue_event, thread_sensitive=False)(
        queue_name,
        event_type,
        **fields,
    )
    try:
        return await asyncio.wait_for(publish, timeout=DEFAULT_PUBLISH_TIMEOUT)
    except TimeoutError:
        logger.error(
            "events.queue_publish_timeout",
            extra={"event_type": event_type, "queue_name": queue_name},
        )
        return False
