"""Tests for the local kiosk's live OCPP connection contract."""

import pytest
from django.test.utils import override_settings
from django.urls import reverse

from apps.ocpp import store
from apps.ocpp.models import Charger


@pytest.fixture(autouse=True)
def clear_connections():
    """Keep the in-memory OCPP connection registry isolated for each test."""

    store.connections.clear()
    yield
    store.connections.clear()


@pytest.mark.django_db
@override_settings(ROOT_URLCONF="apps.ocpp.urls")
def test_kiosk_live_connection_is_loopback_only(client):
    response = client.get(
        reverse("kiosk-live-connection"), REMOTE_ADDR="192.0.2.10"
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "loopback only"}


@pytest.mark.django_db
@override_settings(ROOT_URLCONF="apps.ocpp.urls")
def test_kiosk_live_connection_rejects_public_client_proxied_through_loopback(client):
    response = client.get(
        reverse("kiosk-live-connection"),
        REMOTE_ADDR="127.0.0.1",
        HTTP_X_REAL_IP="198.51.100.10",
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "loopback only"}


@pytest.mark.django_db
@override_settings(ROOT_URLCONF="apps.ocpp.urls")
def test_kiosk_live_connection_accepts_loopback_proxy_client(client):
    response = client.get(
        reverse("kiosk-live-connection"),
        REMOTE_ADDR="127.0.0.1",
        HTTP_X_REAL_IP="::1",
    )

    assert response.status_code == 200


@pytest.mark.django_db
@override_settings(ROOT_URLCONF="apps.ocpp.urls")
def test_kiosk_live_connection_reports_only_active_ocpp_websockets(client):
    connected = Charger.objects.create(charger_id="KIOSK-LIVE-1", connector_id=1)
    Charger.objects.create(charger_id="KIOSK-OFFLINE-1", connector_id=1)
    store.connections[store.identity_key(connected.charger_id, connected.connector_id)] = (
        object()
    )

    response = client.get(
        reverse("kiosk-live-connection"), REMOTE_ADDR="127.0.0.1"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["connected"] is True
    assert payload["charger_count"] == 1
    assert payload["checked_at"]
    assert "no-store" in response["Cache-Control"]


@pytest.mark.django_db
@override_settings(ROOT_URLCONF="apps.ocpp.urls")
def test_kiosk_live_connection_limits_state_to_requested_chargers(client):
    connected = Charger.objects.create(charger_id="KIOSK-LIVE-2", connector_id=1)
    store.connections[store.identity_key(connected.charger_id, connected.connector_id)] = (
        object()
    )

    response = client.get(
        reverse("kiosk-live-connection"),
        {"charger_id": "KIOSK-NOT-CONNECTED"},
        REMOTE_ADDR="127.0.0.1",
    )

    assert response.status_code == 200
    assert response.json()["connected"] is False
    assert response.json()["charger_count"] == 0


@pytest.mark.django_db
@override_settings(ROOT_URLCONF="apps.ocpp.urls")
def test_kiosk_live_connection_deduplicates_requested_charger_ids(client):
    connected = Charger.objects.create(charger_id="KIOSK-LIVE-3", connector_id=1)
    store.connections[store.identity_key(connected.charger_id, connected.connector_id)] = (
        object()
    )

    response = client.get(
        reverse("kiosk-live-connection"),
        [("charger_id", connected.charger_id), ("charger_id", connected.charger_id)],
        REMOTE_ADDR="127.0.0.1",
    )

    assert response.status_code == 200
    assert response.json()["charger_count"] == 1
