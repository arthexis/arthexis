from __future__ import annotations

import pytest

from apps.nodes.models import Node
from apps.xserver.models import XDisplayInstance
from apps.xserver.utils import XServerDetection


@pytest.mark.django_db
def test_refresh_from_system_creates_instance(monkeypatch):
    """Refresh should create an instance when a display server is detected."""

    node = Node.objects.create(hostname="local-node", public_endpoint="local-node")
    monkeypatch.setattr(
        "apps.xserver.models.detect_x_server",
        lambda: XServerDetection(
            display_name=":0",
            host="localhost",
            runtime_scope="local",
            server_type="xorg",
            process_name="Xorg",
            raw_data={"display": ":0"},
        ),
    )

    created, updated = XDisplayInstance.refresh_from_system(node=node)

    assert (created, updated) == (1, 0)
    instance = XDisplayInstance.objects.get(node=node)
    assert instance.server_type == "xorg"


@pytest.mark.django_db
def test_refresh_from_system_deletes_when_undetected(monkeypatch):
    """Refresh should remove stale instances when no server is detected."""

    node = Node.objects.create(hostname="local-node", public_endpoint="local-node")
    XDisplayInstance.objects.create(node=node, display_name=":0")
    monkeypatch.setattr("apps.xserver.models.detect_x_server", lambda: None)

    created, updated = XDisplayInstance.refresh_from_system(node=node)

    assert (created, updated) == (0, 0)
    assert XDisplayInstance.objects.filter(node=node).count() == 0


@pytest.mark.django_db
def test_refresh_from_system_updates_existing_instance(monkeypatch):
    """Refresh should update existing instance fields for the same display."""

    node = Node.objects.create(hostname="local-node", public_endpoint="local-node")
    XDisplayInstance.objects.create(
        node=node,
        display_name=":0",
        host="old-host",
        runtime_scope="remote",
        server_type="x11",
        process_name="Xvfb",
        raw_data={"display": ":0", "version": "old"},
    )

    monkeypatch.setattr(
        "apps.xserver.models.detect_x_server",
        lambda: XServerDetection(
            display_name=":0",
            host="localhost",
            runtime_scope="local",
            server_type="xorg",
            process_name="Xorg",
            raw_data={"display": ":0", "version": "new"},
        ),
    )

    created, updated = XDisplayInstance.refresh_from_system(node=node)

    assert (created, updated) == (0, 1)
    instance = XDisplayInstance.objects.get(node=node, display_name=":0")
    assert instance.host == "localhost"
    assert instance.runtime_scope == "local"
    assert instance.server_type == "xorg"
    assert instance.process_name == "Xorg"
    assert instance.raw_data == {"display": ":0", "version": "new"}
