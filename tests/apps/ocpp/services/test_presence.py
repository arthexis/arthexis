from datetime import timedelta

import pytest
from django.test import override_settings
from django.utils import timezone

from apps.ocpp.models import ChargerConnection
from apps.ocpp.services.presence import (
    configure_heartbeat,
    connection_is_live,
    effective_lease_seconds,
    touch_connection,
)
from tests.apps.ocpp.builders import charger, connection

pytestmark = pytest.mark.django_db


@override_settings(OCPP_PRESENCE_LEASE_SECONDS=60)
def test_stale_persisted_connection_is_not_live() -> None:
    selected = charger("presence-stale")
    live = connection(selected, channel_name="channel-1")
    ChargerConnection.objects.filter(pk=live.pk).update(
        last_seen_at=timezone.now() - timedelta(minutes=2),
        lease_expires_at=timezone.now() - timedelta(minutes=1),
    )
    selected = type(selected).objects.select_related("connection").get(pk=selected.pk)

    assert not connection_is_live(selected)
    assert not type(selected).objects.connected().filter(pk=selected.pk).exists()
    assert type(selected).objects.disconnected().filter(pk=selected.pk).exists()


@override_settings(OCPP_PRESENCE_LEASE_SECONDS=60)
def test_valid_inbound_touch_renews_owned_connection() -> None:
    selected = charger("presence-touch")
    live = connection(selected, channel_name="channel-1")
    ChargerConnection.objects.filter(pk=live.pk).update(
        last_seen_at=timezone.now() - timedelta(minutes=2),
        lease_expires_at=timezone.now() - timedelta(minutes=1),
    )

    touched = touch_connection(charger=selected, channel_name="channel-1")

    assert touched
    live.refresh_from_db()
    assert live.last_seen_at > timezone.now() - timedelta(seconds=10)
    assert live.lease_expires_at > live.last_seen_at


def test_old_channel_cannot_renew_replaced_connection() -> None:
    selected = charger("presence-owner")
    connection(selected, channel_name="new-channel")

    touched = touch_connection(charger=selected, channel_name="old-channel")

    assert not touched


@override_settings(
    OCPP_PRESENCE_LEASE_SECONDS=900,
    OCPP_PRESENCE_HEARTBEAT_MULTIPLIER=3,
    OCPP_PRESENCE_MIN_LEASE_SECONDS=60,
    OCPP_PRESENCE_MAX_LEASE_SECONDS=3600,
)
@pytest.mark.parametrize(
    ("interval", "expected"),
    [(None, 900), (10, 60), (60, 180), (300, 900), (2000, 3600)],
)
def test_effective_lease_uses_fallback_and_clamps_heartbeat_cadence(
    interval: int | None,
    expected: int,
) -> None:
    assert effective_lease_seconds(interval) == expected


@override_settings(
    OCPP_PRESENCE_HEARTBEAT_MULTIPLIER=3,
    OCPP_PRESENCE_MIN_LEASE_SECONDS=60,
    OCPP_PRESENCE_MAX_LEASE_SECONDS=3600,
)
def test_configured_heartbeat_updates_persisted_cadence_and_expiry() -> None:
    selected = charger("presence-heartbeat")
    live = connection(selected, channel_name="channel-1")

    configured = configure_heartbeat(charger=selected, interval_seconds=60)

    assert configured
    live.refresh_from_db()
    assert live.heartbeat_interval_seconds == 60
    assert live.lease_expires_at - live.last_seen_at == timedelta(seconds=180)
