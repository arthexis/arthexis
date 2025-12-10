import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse


pytestmark = pytest.mark.django_db


def test_cpms_dashboard_reachable(client):
    user_model = get_user_model()
    user = user_model.objects.create_user(
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

    from apps.ocpp.models import Charger
    from django.utils import timezone

    heartbeat = timezone.now()
    Charger.objects.create(
        charger_id="DASH-1",
        last_heartbeat=heartbeat,
    )

    response = client.get(reverse("ocpp:ocpp-dashboard"))

    assert response.status_code == 200
    assert response.context["chargers"][0]["last_seen"] == heartbeat
