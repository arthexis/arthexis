from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.ocpp.models import Charger, Transaction
from apps.ocpp.views.dashboard import _attach_charger_group_entry

pytestmark = [pytest.mark.django_db]


def test_dashboard_hides_demo_notice_for_logged_in_staff(client):
    user = get_user_model().objects.create_user(
        username="dashboard-user", password="pass", is_staff=True
    )
    client.force_login(user)

    response = client.get(reverse("ocpp:ocpp-dashboard"))

    assert response.status_code == 200
    assert '<div id="demo-notice"' not in response.content.decode()


def test_dashboard_grouping_keeps_children_when_parent_is_processed_last():
    charger_groups = []
    group_lookup = {}
    child_entry = {
        "charger": SimpleNamespace(charger_id="CP-AGG-ORDER", connector_id=1)
    }
    parent_entry = {
        "charger": SimpleNamespace(charger_id="CP-AGG-ORDER", connector_id=None)
    }

    _attach_charger_group_entry(charger_groups, group_lookup, child_entry)
    _attach_charger_group_entry(charger_groups, group_lookup, parent_entry)

    assert charger_groups == [{"parent": parent_entry, "children": [child_entry]}]
    assert group_lookup["CP-AGG-ORDER"] is charger_groups[0]


def test_dashboard_aggregate_row_uses_connector_totals_and_last_heartbeat(client):
    user = get_user_model().objects.create_user(
        username="dashboard-aggregate-user", password="pass", is_staff=True
    )
    client.force_login(user)

    last_heartbeat = timezone.now()
    aggregate = Charger.objects.create(
        charger_id="CP-AGG-1",
        connector_id=None,
        last_heartbeat=last_heartbeat,
        last_status="Available",
    )
    connector_a = Charger.objects.create(
        charger_id="CP-AGG-1",
        connector_id=1,
        last_heartbeat=last_heartbeat,
        last_status="Charging",
    )
    connector_b = Charger.objects.create(
        charger_id="CP-AGG-1",
        connector_id=2,
        last_heartbeat=last_heartbeat,
        last_status="Charging",
    )

    Transaction.objects.create(
        charger=aggregate,
        connector_id=None,
        start_time=timezone.now(),
        stop_time=timezone.now(),
        meter_start=0,
        meter_stop=1000,
    )
    Transaction.objects.create(
        charger=connector_a,
        connector_id=1,
        start_time=timezone.now(),
        stop_time=timezone.now(),
        meter_start=0,
        meter_stop=2000,
    )
    Transaction.objects.create(
        charger=connector_b,
        connector_id=2,
        start_time=timezone.now(),
        stop_time=timezone.now(),
        meter_start=0,
        meter_stop=3000,
    )

    response = client.get(reverse("ocpp:ocpp-dashboard") + "?partial=table")

    assert response.status_code == 200
    html = response.content.decode()
    assert "5.00" in html
    assert "1.00" not in html
    assert '<td class="text-muted">—</td>' in html
