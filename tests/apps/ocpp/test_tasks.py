from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from apps.ocpp.models import Charger
from apps.ocpp.tasks import refresh_stale_connections
from tests.apps.ocpp.builders import charger


class OcppMaintenanceTaskTests(TestCase):
    def test_stale_connection_task_only_changes_stale_chargers(self) -> None:
        stale = charger("charger-stale")
        fresh = charger("charger-fresh")
        Charger.objects.filter(pk=stale.pk).update(
            connected_at=timezone.now() - timedelta(hours=3)
        )
        Charger.objects.filter(pk=fresh.pk).update(connected_at=timezone.now())

        self.assertEqual(refresh_stale_connections(), 1)

        stale.refresh_from_db()
        fresh.refresh_from_db()
        self.assertIsNone(stale.connected_at)
        self.assertIsNotNone(fresh.connected_at)
