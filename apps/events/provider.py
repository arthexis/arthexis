from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .queues import publish_queue_event


def publish_event(
    event_type: str,
    /,
    *,
    queue: str | None = None,
    data: Mapping[str, Any] | None = None,
) -> bool:
    """Publish a structured event through Arthexis' queue transport.

    Event types route to a queue of the same name unless the caller explicitly
    overrides the queue.  This keeps the provider generic while preserving the
    dedicated queue convention used by existing OCPP events.
    """

    queue_name = (queue or event_type).strip()
    if not queue_name:
        raise ValueError("event queue cannot be empty")
    event_name = event_type.strip()
    if not event_name:
        raise ValueError("event type cannot be empty")
    return publish_queue_event(queue_name, event_name, **dict(data or {}))


pub = publish_event

__all__ = ["pub", "publish_event"]
