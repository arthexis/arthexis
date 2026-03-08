from django.urls import reverse

import pytest

from apps.bluetooth.admin import DEFAULT_DISCOVERY_TIMEOUT_S


@pytest.mark.django_db
def test_discover_page_defaults_timeout_to_60_seconds(admin_client):
    """Discover form should render with the new 60-second default timeout."""

    response = admin_client.get(reverse("admin:bluetooth_bluetoothdevice_discover"))

    assert response.status_code == 200
    assert b'name="timeout_s"' in response.content
    assert f'value="{DEFAULT_DISCOVERY_TIMEOUT_S}"'.encode() in response.content


@pytest.mark.django_db
def test_discover_post_uses_60_seconds_when_timeout_missing(admin_client, monkeypatch):
    """Regression: empty timeout input should fall back to 60 seconds for discovery."""

    captured_timeout = {}

    def fake_discover_and_sync_devices(*, timeout_s):
        captured_timeout["value"] = timeout_s
        return {"count": 0, "created": 0, "updated": 0}

    monkeypatch.setattr(
        "apps.bluetooth.admin.discover_and_sync_devices",
        fake_discover_and_sync_devices,
    )

    response = admin_client.post(reverse("admin:bluetooth_bluetoothdevice_discover"), {})

    assert response.status_code == 200
    assert captured_timeout["value"] == DEFAULT_DISCOVERY_TIMEOUT_S
