"""Heartbeat-aware lease-based charger presence tracking."""

from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from apps.ocpp.models import Charger, ChargerConnection


def effective_lease_seconds(heartbeat_interval_seconds: int | None = None) -> int:
    """Resolve one connection's presence lease from negotiated heartbeat cadence."""
    if heartbeat_interval_seconds is None or heartbeat_interval_seconds <= 0:
        return settings.OCPP_PRESENCE_LEASE_SECONDS
    lease = heartbeat_interval_seconds * settings.OCPP_PRESENCE_HEARTBEAT_MULTIPLIER
    return max(
        settings.OCPP_PRESENCE_MIN_LEASE_SECONDS,
        min(lease, settings.OCPP_PRESENCE_MAX_LEASE_SECONDS),
    )


def lease_expiry(*, observed_at=None, heartbeat_interval_seconds: int | None = None):
    observed_at = observed_at or timezone.now()
    return observed_at + timedelta(
        seconds=effective_lease_seconds(heartbeat_interval_seconds)
    )


def connection_is_live(charger: Charger) -> bool:
    connection = getattr(charger, "connection", None)
    return bool(
        connection
        and connection.lease_expires_at >= timezone.now()
    )


def configure_heartbeat(*, charger: Charger, interval_seconds: int) -> bool:
    """Persist the negotiated heartbeat cadence for the current connection."""
    if (
        isinstance(interval_seconds, bool)
        or not isinstance(interval_seconds, int)
        or interval_seconds <= 0
    ):
        raise ValueError("heartbeat interval must be a positive integer")
    now = timezone.now()
    updated = ChargerConnection.objects.filter(charger=charger).update(
        heartbeat_interval_seconds=interval_seconds,
        last_seen_at=now,
        lease_expires_at=lease_expiry(
            observed_at=now,
            heartbeat_interval_seconds=interval_seconds,
        ),
    )
    if updated:
        Charger.objects.filter(pk=charger.pk).update(connected_at=now)
    return bool(updated)


def touch_connection(*, charger: Charger, channel_name: str) -> bool:
    """Renew only the currently owned connection using its negotiated cadence."""
    connection = ChargerConnection.objects.filter(
        charger=charger,
        channel_name=channel_name,
    ).only("pk", "heartbeat_interval_seconds").first()
    if connection is None:
        return False
    now = timezone.now()
    updated = ChargerConnection.objects.filter(
        pk=connection.pk,
        channel_name=channel_name,
    ).update(
        last_seen_at=now,
        lease_expires_at=lease_expiry(
            observed_at=now,
            heartbeat_interval_seconds=connection.heartbeat_interval_seconds,
        ),
    )
    if updated:
        Charger.objects.filter(pk=charger.pk).update(connected_at=now)
    return bool(updated)
