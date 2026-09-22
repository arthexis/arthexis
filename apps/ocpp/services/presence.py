"""Lease-based charger presence tracking."""

from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from apps.ocpp.models import Charger, ChargerConnection


def presence_cutoff():
    return timezone.now() - timedelta(seconds=settings.OCPP_PRESENCE_LEASE_SECONDS)


def connection_is_live(charger: Charger) -> bool:
    connection = getattr(charger, "connection", None)
    return bool(connection and connection.last_seen_at >= presence_cutoff())


def touch_connection(*, charger: Charger, channel_name: str) -> bool:
    now = timezone.now()
    updated = ChargerConnection.objects.filter(
        charger=charger,
        channel_name=channel_name,
    ).update(last_seen_at=now)
    if updated:
        Charger.objects.filter(pk=charger.pk).update(connected_at=now)
    return bool(updated)
