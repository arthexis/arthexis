from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.ocpp.models import ChargerConnection
from apps.ocpp.services.presence import connection_is_live, touch_connection
from tests.apps.ocpp.builders import charger, connection


class PresenceLeaseTests(TestCase):
    @override_settings(OCPP_PRESENCE_LEASE_SECONDS=60)
    def test_stale_persisted_connection_is_not_live(self) -> None:
        selected = charger("presence-stale")
        live = connection(selected, channel_name="channel-1")
        ChargerConnection.objects.filter(pk=live.pk).update(
            last_seen_at=timezone.now() - timedelta(minutes=2)
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
            last_seen_at=timezone.now() - timedelta(minutes=2)
        )

        touched = touch_connection(charger=selected, channel_name="channel-1")

        self.assertTrue(touched)
        live.refresh_from_db()
        self.assertGreater(live.last_seen_at, timezone.now() - timedelta(seconds=10))

    def test_old_channel_cannot_renew_replaced_connection(self) -> None:
        selected = charger("presence-owner")
        connection(selected, channel_name="new-channel")

        touched = touch_connection(charger=selected, channel_name="old-channel")

        self.assertFalse(touched)
