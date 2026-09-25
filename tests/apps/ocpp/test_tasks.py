from datetime import timedelta

import pytest
from django.utils import timezone

from apps.ocpp.models import Charger
from apps.ocpp.tasks import refresh_stale_connections
from tests.apps.ocpp.builders import charger

pytestmark = pytest.mark.django_db


def test_stale_connection_task_only_changes_stale_chargers() -> None:
    stale = charger("charger-stale")
    fresh = charger("charger-fresh")
    Charger.objects.filter(pk=stale.pk).update(
        connected_at=timezone.now() - timedelta(hours=3)
    )
    Charger.objects.filter(pk=fresh.pk).update(connected_at=timezone.now())

    assert refresh_stale_connections() == 1

    stale.refresh_from_db()
    fresh.refresh_from_db()
    assert stale.connected_at is None
    assert fresh.connected_at is not None
