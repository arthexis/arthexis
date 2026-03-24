import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.ocpp.models import Charger
from apps.ocpp.models.location import Location


@pytest.mark.django_db
def test_charging_station_map_renders_charger_locations(monkeypatch, client):
    user = get_user_model().objects.create_user("map-user", password="secret")
    client.force_login(user)

    location = Location.objects.create(name="North Depot", latitude=19.432608, longitude=-99.133209)
    charger = Charger.objects.create(charger_id="MAP-001", connector_id=1, location=location)

    monkeypatch.setattr("apps.ocpp.views.public.require_site_operator_or_staff", lambda request: None)
    monkeypatch.setattr(
        "apps.ocpp.views.public._visible_chargers",
        lambda _user: Charger.objects.filter(pk=charger.pk),
    )

    response = client.get(reverse("ocpp:charging-station-map"))

    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert "North Depot" in content
    assert "destination=19.432608,-99.133209" in content


@pytest.mark.django_db
def test_charging_station_map_shows_empty_state(monkeypatch, client):
    user = get_user_model().objects.create_user("empty-map-user", password="secret")
    client.force_login(user)

    monkeypatch.setattr("apps.ocpp.views.public.require_site_operator_or_staff", lambda request: None)
    monkeypatch.setattr("apps.ocpp.views.public._visible_chargers", lambda _user: Charger.objects.none())

    response = client.get(reverse("ocpp:charging-station-map"))

    assert response.status_code == 200
    assert "No charging locations available yet." in response.content.decode("utf-8")


@pytest.mark.django_db
def test_ocpp_location_add_current_admin_view_uses_ocpp_changelist_link(admin_client):
    response = admin_client.get(reverse("admin:ocpp_location_add_current"))

    assert response.status_code == 200
    assert reverse("admin:ocpp_location_changelist") in response.content.decode("utf-8")
