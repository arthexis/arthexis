from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.ocpp.models import ChargerConnection
from apps.ocpp.services.presence import (
    configure_heartbeat,
    connection_is_live,
    effective_lease_seconds,
    touch_connection,
)
from tests.apps.ocpp.builders import charger, connection


class PresenceLeaseTests(TestCase):
    @override_settings(OCPP_PRESENCE_LEASE_SECONDS=60)
    def test_stale_persisted_connection_is_not_live(self) -> None:
        selected = charger("presence-stale")
        live = connection(selected, channel_name="channel-1")
        ChargerConnection.objects.filter(pk=live.pk).update(
            last_seen_at=timezone.now() - timedelta(minutes=2),
            lease_expires_at=timezone.now() - timedelta(minutes=1),
        )
        selected = type(selected).objects.select_related("connection").get(pk=selected.pk)

        self.assertFalse(connection_is_live(selected))
        self.assertFalse(type(selected).objects.connected().filter(pk=selected.pk).exists())
        self.assertTrue(type(selected).objects.disconnected().filter(pk=selected.pk).exists())

    @override_settings(OCPP_PRESENCE_LEASE_SECONDS=60)
    def test_valid_inbound_touch_renews_owned_connection(self) -> None:
        selected = charger("presence-touch")
        live = connection(selected, channel_name="channel-1")
        ChargerConnection.objects.filter(pk=live.pk).update(
            last_seen_at=timezone.now() - timedelta(minutes=2),
            lease_expires_at=timezone.now() - timedelta(minutes=1),
        )

        touched = touch_connection(charger=selected, channel_name="channel-1")

        self.assertTrue(touched)
        live.refresh_from_db()
        self.assertGreater(live.last_seen_at, timezone.now() - timedelta(seconds=10))
        self.assertGreater(live.lease_expires_at, live.last_seen_at)

    def test_old_channel_cannot_renew_replaced_connection(self) -> None:
        selected = charger("presence-owner")
        connection(selected, channel_name="new-channel")

        touched = touch_connection(charger=selected, channel_name="old-channel")

        self.assertFalse(touched)


    @override_settings(
        OCPP_PRESENCE_LEASE_SECONDS=900,
        OCPP_PRESENCE_HEARTBEAT_MULTIPLIER=3,
        OCPP_PRESENCE_MIN_LEASE_SECONDS=60,
        OCPP_PRESENCE_MAX_LEASE_SECONDS=3600,
    )
    def test_effective_lease_uses_fallback_and_clamps_heartbeat_cadence(self) -> None:
        self.assertEqual(effective_lease_seconds(), 900)
        self.assertEqual(effective_lease_seconds(10), 60)
        self.assertEqual(effective_lease_seconds(60), 180)
        self.assertEqual(effective_lease_seconds(300), 900)
        self.assertEqual(effective_lease_seconds(2000), 3600)

    @override_settings(
        OCPP_PRESENCE_HEARTBEAT_MULTIPLIER=3,
        OCPP_PRESENCE_MIN_LEASE_SECONDS=60,
        OCPP_PRESENCE_MAX_LEASE_SECONDS=3600,
    )
    def test_configured_heartbeat_updates_persisted_cadence_and_expiry(self) -> None:
        selected = charger("presence-heartbeat")
        live = connection(selected, channel_name="channel-1")

        configured = configure_heartbeat(charger=selected, interval_seconds=60)

        self.assertTrue(configured)
        live.refresh_from_db()
        self.assertEqual(live.heartbeat_interval_seconds, 60)
        lease_length = live.lease_expires_at - live.last_seen_at
        self.assertEqual(lease_length, timedelta(seconds=180))
