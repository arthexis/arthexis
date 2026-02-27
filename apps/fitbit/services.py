"""Service helpers for Fitbit data storage and Net Message forwarding."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from django.db import models
from django.db.models import QuerySet
from django.utils import timezone

from apps.fitbit.models import FitbitConnection, FitbitHealthSample, FitbitNetMessageDelivery
from apps.nodes.models import NetMessage


class FitbitPayloadError(ValueError):
    """Raised when a Fitbit payload does not satisfy minimum shape requirements."""


def record_health_payload(
    *,
    connection: FitbitConnection,
    resource: str,
    payload: Mapping[str, object],
    observed_at: datetime | None = None,
) -> FitbitHealthSample:
    """Persist a Fitbit health sample from a polled endpoint payload."""
    normalized_resource = resource.strip()
    if not normalized_resource:
        raise FitbitPayloadError("resource is required")

    if not isinstance(payload, Mapping):
        raise FitbitPayloadError("payload must be a mapping")

    return FitbitHealthSample.objects.create(
        connection=connection,
        resource=normalized_resource[:64],
        payload=dict(payload),
        observed_at=observed_at or timezone.now(),
    )


def dispatch_net_messages_to_connections(
    *,
    connection: FitbitConnection | None = None,
    limit: int = 25,
) -> list[FitbitNetMessageDelivery]:
    """Create Fitbit delivery records from pending fitbit-targeted Net Messages."""
    if limit <= 0:
        return []

    target_connections: QuerySet[FitbitConnection] = FitbitConnection.objects.filter(
        is_active=True
    )
    if connection is not None:
        target_connections = target_connections.filter(pk=connection.pk)

    if not target_connections.exists():
        return []

    now = timezone.now()
    messages = (
        NetMessage.objects.filter(lcd_channel_type="fitbit")
        .filter(models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=now))
        .order_by("-created")[:limit]
    )

    deliveries: list[FitbitNetMessageDelivery] = []
    for net_message in messages:
        for conn in target_connections:
            rendered_text = f"{net_message.subject}: {net_message.body}".strip(": ")[:256]
            delivery, created = FitbitNetMessageDelivery.objects.get_or_create(
                connection=conn,
                net_message=net_message,
                defaults={
                    "rendered_text": rendered_text,
                    "status": FitbitNetMessageDelivery.Status.SENT,
                },
            )
            if created:
                deliveries.append(delivery)

    return deliveries
