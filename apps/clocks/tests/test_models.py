import pytest

from apps.clocks.models import ClockDevice
from apps.nodes.models import Node


@pytest.mark.django_db
def test_refresh_from_system_creates_and_updates_devices(monkeypatch):
    """Refresh should create once and remain stable across identical scans."""

    monkeypatch.setattr(
        "apps.clocks.models.is_feature_active_for_node",
        lambda *, node, slug: True,
    )

    node = Node.objects.create(hostname="local")
    sample = """
         0 1 2 3 4 5 6 7 8 9 a b c d e f
60: -- -- -- -- -- -- -- -- 68 -- -- -- -- -- -- --
"""

    created, updated = ClockDevice.refresh_from_system(
        node=node, scanner=lambda bus: sample
    )

    assert (created, updated) == (1, 0)
    device = ClockDevice.objects.get(node=node)
    assert device.address == "0x68"
    assert device.bus == 1

    created, updated = ClockDevice.refresh_from_system(
        node=node, scanner=lambda bus: sample
    )

    assert (created, updated) == (0, 0)


@pytest.mark.django_db
def test_refresh_from_system_removes_stale_devices(monkeypatch):
    """Refresh should remove persisted devices absent from the latest scan."""

    monkeypatch.setattr(
        "apps.clocks.models.is_feature_active_for_node",
        lambda *, node, slug: True,
    )

    node = Node.objects.create(hostname="local")
    ClockDevice.objects.create(node=node, bus=2, address="0x10", description="Old", raw_info="")

    created, updated = ClockDevice.refresh_from_system(node=node, scanner=lambda bus: "")

    assert (created, updated) == (0, 0)
    assert ClockDevice.objects.filter(node=node).count() == 0
