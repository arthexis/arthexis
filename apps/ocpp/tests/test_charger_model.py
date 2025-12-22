import datetime as dt
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.ocpp.models import Charger
from apps.nodes.models import Node


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


def test_create_charger_ignores_stale_local_node_cache():
    mac = Node.get_current_mac()
    stale_node = Node(
        id=9999,
        hostname="stale",
        mac_address=mac,
        current_relation=Node.Relation.SELF,
    )
    Node._local_cache[mac] = (stale_node, timezone.now() + timedelta(hours=1))

    charger = Charger.objects.create(charger_id="CH-3")

    assert charger.manager_node_id is None
    Node._local_cache.clear()
