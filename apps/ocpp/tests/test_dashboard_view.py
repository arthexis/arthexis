from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.ocpp.models import Charger, Transaction


pytestmark = [pytest.mark.django_db, pytest.mark.regression]


def test_cpms_dashboard_reachable(client):
    user = get_user_model().objects.create_user(
        username="dashboard-user", email="dashboard@example.com", password="pass"
    )
    client.force_login(user)

    response = client.get(reverse("ocpp:ocpp-dashboard"))

    assert response.status_code == 200


def test_dashboard_includes_last_seen(client, django_user_model):
    user = django_user_model.objects.create_user(
        username="dashboard-user-2", email="dashboard2@example.com", password="pass"
    )
    client.force_login(user)

    heartbeat = timezone.now()
    Charger.objects.create(
        charger_id="DASH-1",
        last_heartbeat=heartbeat,
    )

    response = client.get(reverse("ocpp:ocpp-dashboard"))

    assert response.status_code == 200
    assert response.context["chargers"][0]["last_seen"] == heartbeat


def test_dashboard_includes_last_session_date_from_start_time(client, django_user_model):
    user = django_user_model.objects.create_user(
        username="dashboard-user-4", email="dashboard4@example.com", password="pass"
    )
    client.force_login(user)

    charger = Charger.objects.create(charger_id="DASH-SESSION")
    session_start = timezone.now() - timedelta(days=1)
    Transaction.objects.create(charger=charger, start_time=session_start)

    response = client.get(reverse("ocpp:ocpp-dashboard"))

    assert response.status_code == 200
    assert response.context["chargers"][0]["last_session"] == session_start


def test_dashboard_includes_last_session_date_from_stop_time(client, django_user_model):
    user = django_user_model.objects.create_user(
        username="dashboard-user-5", email="dashboard5@example.com", password="pass"
    )
    client.force_login(user)

    charger = Charger.objects.create(charger_id="DASH-SESSION-STOP")
    session_start = timezone.now() - timedelta(days=2)
    session_stop = timezone.now() - timedelta(days=1)
    Transaction.objects.create(
        charger=charger, start_time=session_start, stop_time=session_stop
    )

    response = client.get(reverse("ocpp:ocpp-dashboard"))

    assert response.status_code == 200
    assert response.context["chargers"][0]["last_session"] == session_stop


def test_dashboard_allows_anonymous_terminal_role(client, monkeypatch):
    from types import SimpleNamespace

    from apps.nodes.models import Node, NodeRole

    terminal_role = NodeRole.objects.create(name="Terminal")
    terminal_node = SimpleNamespace(role=terminal_role)
    monkeypatch.setattr(Node, "get_local", classmethod(lambda cls: terminal_node))

    response = client.get(reverse("ocpp:ocpp-dashboard"))

    assert response.status_code == 200


def test_dashboard_surfaces_charging_limit(client, django_user_model):
    user = django_user_model.objects.create_user(
        username="dashboard-user-3", email="dashboard3@example.com", password="pass"
    )
    client.force_login(user)

    Charger.objects.create(
        charger_id="DASH-LIMIT",
        last_charging_limit_source="EMS",
        last_charging_limit={
            "chargingLimit": {
                "chargingLimitSource": "EMS",
                "isGridCritical": False,
            },
            "chargingSchedule": [
                {
                    "chargingRateUnit": "A",
                    "chargingSchedulePeriod": [{"startPeriod": 0, "limit": 25}],
                }
            ],
        },
        last_charging_limit_at=timezone.now(),
    )

    response = client.get(reverse("ocpp:ocpp-dashboard"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "EMS" in content
    assert "Limit" in content
