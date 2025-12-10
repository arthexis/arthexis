import datetime as dt

import pytest
from django.utils import timezone

from apps.ocpp.models import Charger


pytestmark = pytest.mark.django_db


def test_last_seen_prefers_status_timestamp():
    timestamp = timezone.now()
    charger = Charger.objects.create(
        charger_id="CH-1",
        last_status_timestamp=timestamp,
        last_heartbeat=timestamp - dt.timedelta(minutes=5),
    )

    assert charger.last_seen == timestamp


def test_last_seen_falls_back_to_heartbeat():
    heartbeat = timezone.now()
    charger = Charger.objects.create(
        charger_id="CH-2",
        last_status_timestamp=None,
        last_heartbeat=heartbeat,
    )

    assert charger.last_seen == heartbeat
