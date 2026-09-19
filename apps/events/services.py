"""Structured event persistence used by retained domain services."""

import json
import logging
from collections.abc import Mapping

from django.db import DatabaseError

from apps.events.models import EventEnvelope

logger = logging.getLogger(__name__)


def publish(
    *, event_type: str, producer: str, payload: Mapping[str, object]
) -> EventEnvelope:
    """Persist one JSON event envelope after validating its public shape."""
    if not event_type:
        raise ValueError("Event type is required.")
    if not producer:
        raise ValueError("Event producer is required.")
    serialized_payload = dict(payload)
    json.dumps(serialized_payload)
    return EventEnvelope.objects.create(
        event_type=event_type,
        producer=producer,
        payload=serialized_payload,
    )


def publish_safely(
    *, event_type: str, producer: str, payload: Mapping[str, object]
) -> EventEnvelope | None:
    """Publish without turning an OCPP protocol response into a server failure."""
    try:
        return publish(event_type=event_type, producer=producer, payload=payload)
    except (DatabaseError, TypeError, ValueError):
        logger.exception("Could not publish event %s from %s", event_type, producer)
        return None
