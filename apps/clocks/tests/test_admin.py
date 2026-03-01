import pytest

from django.urls import reverse

from apps.clocks.models import ClockDevice
from apps.nodes.models import Node, NodeFeature, NodeFeatureAssignment


@pytest.mark.django_db
def test_find_devices_persists_gpio_rtc_assignment_when_auto_detected(admin_client, monkeypatch):
    """Clock discovery should persist gpio-rtc assignment even when auto-detection reports enabled."""

    node = Node.objects.create(hostname="local-node", public_endpoint="local-node")
    feature = NodeFeature.objects.create(slug="gpio-rtc", display="GPIO RTC")
    monkeypatch.setattr(Node, "get_local", classmethod(lambda cls: node))
    monkeypatch.setattr("apps.clocks.admin.has_clock_device", lambda: True)
    monkeypatch.setattr(
        "apps.nodes.models.features.has_clock_device",
        lambda: True,
    )
    monkeypatch.setattr(
        ClockDevice,
        "refresh_from_system",
        lambda **kwargs: (0, 0, [], []),
    )

    response = admin_client.post(reverse("admin:clocks_clockdevice_find_devices"))

    assert response.status_code == 302
    assert NodeFeatureAssignment.objects.filter(node=node, feature=feature).exists()
