"""Tests for public charger page connector-only navigation behavior."""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.ocpp.models import Charger


pytestmark = [pytest.mark.django_db]


def test_charger_page_redirects_station_path_to_first_connector(client):
    """Station-level public route should redirect to a concrete connector page."""

    parent = Charger.objects.create(charger_id="REDIRECT-CP", connector_id=None)
    Charger.objects.create(charger_id=parent.charger_id, connector_id=2)
    first_connector = Charger.objects.create(charger_id=parent.charger_id, connector_id=1)

    response = client.get(reverse("ocpp:charger-page", args=[parent.charger_id]))

    assert response.status_code == 302
    assert response.url == reverse(
        "ocpp:charger-page-connector",
        args=[parent.charger_id, first_connector.connector_slug],
    )


def test_charger_page_renders_connector_switch_links_without_station_entry(client):
    """Connector page should show only connector-to-connector navigation links."""

    Charger.objects.create(charger_id="REDIRECT-CP-2", connector_id=None)
    first_connector = Charger.objects.create(charger_id="REDIRECT-CP-2", connector_id=1)
    second_connector = Charger.objects.create(charger_id="REDIRECT-CP-2", connector_id=2)

    response = client.get(
        reverse(
            "ocpp:charger-page-connector",
            args=[first_connector.charger_id, first_connector.connector_slug],
        )
    )

    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert reverse(
        "ocpp:charger-page-connector",
        args=[first_connector.charger_id, first_connector.connector_slug],
    ) in content
    assert reverse(
        "ocpp:charger-page-connector",
        args=[second_connector.charger_id, second_connector.connector_slug],
    ) in content
    station_href = f'href="{reverse("ocpp:charger-page", args=[first_connector.charger_id])}"'
    assert station_href not in content


def test_charger_page_redirects_station_path_to_first_accessible_connector(client):
    """Station redirect should skip connectors hidden from the current user."""

    parent = Charger.objects.create(charger_id="REDIRECT-CP-3", connector_id=None)
    hidden_connector = Charger.objects.create(charger_id=parent.charger_id, connector_id=1)
    visible_connector = Charger.objects.create(charger_id=parent.charger_id, connector_id=2)

    user_model = get_user_model()
    owner = user_model.objects.create_user(username="connector-owner")
    hidden_connector.owner_users.add(owner)

    response = client.get(reverse("ocpp:charger-page", args=[parent.charger_id]))

    assert response.status_code == 302
    assert response.url == reverse(
        "ocpp:charger-page-connector",
        args=[parent.charger_id, visible_connector.connector_slug],
    )


def test_public_charger_page_loads_brand_font_pair(client):
    """Public OCPP landing should include the branded heading/body font pair."""

    charger = Charger.objects.create(charger_id="REDIRECT-CP-4", connector_id=1)

    response = client.get(
        reverse(
            "ocpp:charger-page-connector",
            args=[charger.charger_id, charger.connector_slug],
        )
    )

    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert "fonts.googleapis.com" in content
    assert "family=Space+Grotesk" in content
    assert "family=Instrument+Sans" in content
