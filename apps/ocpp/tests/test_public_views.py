import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.ocpp.models import Charger


@pytest.mark.django_db
def test_connector_status_view_handles_connector_slug(client):
    user = get_user_model().objects.create_user(username="tester", password="pass")
    Charger.objects.create(charger_id="LOCALSIM-0001", connector_id=None)
    Charger.objects.create(charger_id="LOCALSIM-0001", connector_id=2)

    client.force_login(user)
    url = reverse("ocpp:charger-status-connector", args=["LOCALSIM-0001", "2"])
    response = client.get(url)

    assert response.status_code == 200
    assert response.context["connector_slug"] == "2"
