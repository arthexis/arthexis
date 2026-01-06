from datetime import datetime, timezone as dt_timezone

import pytest
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from apps.clocks.models import ClockDevice
from apps.nodes.models import Node
from config import context_processors as cp


@pytest.mark.django_db
def test_site_and_node_clock_defaults(monkeypatch):
    request = RequestFactory().get("/admin/")

    fixed_now = datetime(2024, 1, 2, 3, 4, 5, tzinfo=dt_timezone.utc)
    monkeypatch.setattr(cp.timezone, "now", lambda: fixed_now)
    monkeypatch.setattr(
        "apps.clocks.utils.read_hardware_clock_time", lambda: None
    )

    context = cp.site_and_node(request)

    assert context["admin_clock_time"] == fixed_now
    assert context["admin_clock_url"] == reverse("admin:clocks_clockdevice_changelist")
    assert context["admin_clock_timezone"] == timezone.get_current_timezone_name()


@pytest.mark.django_db
def test_site_and_node_prefers_clock_device(monkeypatch):
    node = Node.objects.create(hostname="local")
    device = ClockDevice.objects.create(
        node=node,
        bus=1,
        address="0x20",
        description="RTC",
        raw_info="",
        enable_public_view=True,
    )

    request = RequestFactory().get("/admin/")
    fixed_clock = datetime(2025, 6, 7, 8, 9, 10, tzinfo=dt_timezone.utc)

    monkeypatch.setattr(Node, "get_local", staticmethod(lambda: node))
    monkeypatch.setattr(
        "apps.clocks.utils.read_hardware_clock_time", lambda: fixed_clock
    )

    context = cp.site_and_node(request)

    assert context["badge_node"] == node
    assert context["admin_clock_time"] == fixed_clock.astimezone(
        timezone.get_current_timezone()
    )
    assert context["admin_clock_url"] == reverse(
        "clockdevice-public-view", args=[device.public_view_slug]
    )
