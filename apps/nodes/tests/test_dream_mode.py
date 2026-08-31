from __future__ import annotations

import json
from io import StringIO
from types import SimpleNamespace

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.nodes import dream_mode
from apps.nodes.models import Node


def test_gway_001_allows_network_on_dream_mode_without_high_throughput():
    node = SimpleNamespace(
        public_endpoint="gway-001",
        hostname="gway-001",
        network_hostname="gway-001.local",
    )

    decision = dream_mode.evaluate_dream_mode(
        node=node,
        networks_enabled=True,
        inventory_devices=[{"name": "sda1", "label": "BASTION"}],
    )

    assert decision.allowed is True
    assert decision.networks_enabled is True
    assert decision.high_throughput_peripherals == ()


def test_gway_001_blocks_network_on_dream_mode_with_camera_present():
    node = SimpleNamespace(public_endpoint="gway-001", hostname="gway-001")

    decision = dream_mode.evaluate_dream_mode(
        node=node,
        networks_enabled=True,
        inventory_devices=[
            {
                "name": "video0",
                "model": "USB Camera",
                "claims": ["camera"],
            }
        ],
    )

    assert decision.allowed is False
    assert decision.blockers == ("high-throughput-peripherals-connected",)
    assert decision.high_throughput_peripherals == ("USB Camera",)


def test_high_throughput_detection_ignores_metadata_keys():
    peripherals = dream_mode.high_throughput_peripherals(
        [
            {
                "name": "safe-device",
                "video_enabled": False,
                "audio_channels": 0,
                "metadata": {"camera_allowed": False},
            }
        ]
    )

    assert peripherals == ()


def test_other_nodes_keep_network_on_dream_mode_blocked():
    node = SimpleNamespace(public_endpoint="gway-002", hostname="gway-002")

    decision = dream_mode.evaluate_dream_mode(
        node=node,
        networks_enabled=True,
        inventory_devices=[],
    )

    assert decision.allowed is False
    assert decision.blockers == ("network-on-not-allowed-for-node",)


def test_network_off_dream_mode_preserves_existing_behavior_with_warning():
    node = SimpleNamespace(public_endpoint="satellite-001", hostname="satellite-001")

    decision = dream_mode.evaluate_dream_mode(
        node=node,
        networks_enabled=False,
        inventory_devices=[{"label": "Webcam", "claims": ["camera"]}],
    )

    assert decision.allowed is True
    assert decision.networks_enabled is False
    assert decision.warnings == ("high-throughput-peripherals-connected",)


def test_network_on_dream_mode_fails_closed_when_inventory_unavailable(monkeypatch):
    node = SimpleNamespace(public_endpoint="gway-001", hostname="gway-001")

    def fail_inventory(*, refresh=False):
        raise dream_mode.usb_inventory.UsbInventoryError("missing tools")

    monkeypatch.setattr(dream_mode.usb_inventory, "state_or_refresh", fail_inventory)

    decision = dream_mode.evaluate_dream_mode(node=node, networks_enabled=True)

    assert decision.allowed is False
    assert decision.blockers == ("high-throughput-inventory-unavailable",)
    assert "missing tools" in decision.reason


@pytest.mark.django_db
def test_node_dream_mode_check_json_allows_gway_001_network_on(monkeypatch):
    Node._local_cache.clear()
    node = Node.objects.create(
        hostname="gway-001",
        public_endpoint="gway-001",
        current_relation=Node.Relation.SELF,
    )
    monkeypatch.setattr(Node, "get_local", classmethod(lambda cls: node))
    monkeypatch.setattr(
        dream_mode.usb_inventory,
        "state_or_refresh",
        lambda *, refresh=False: {"devices": []},
    )

    stdout = StringIO()
    call_command("node", "dream-mode-check", "--networks-on", "--json", stdout=stdout)

    payload = json.loads(stdout.getvalue())
    assert payload["allowed"] is True
    assert payload["networks_enabled"] is True
    assert payload["node"] == "gway-001"


@pytest.mark.django_db
def test_node_dream_mode_check_blocks_other_nodes_network_on(monkeypatch):
    Node._local_cache.clear()
    node = Node.objects.create(
        hostname="terminal",
        public_endpoint="terminal",
        current_relation=Node.Relation.SELF,
    )
    monkeypatch.setattr(Node, "get_local", classmethod(lambda cls: node))
    monkeypatch.setattr(
        dream_mode.usb_inventory,
        "state_or_refresh",
        lambda *, refresh=False: {"devices": []},
    )

    with pytest.raises(CommandError, match="restricted to gway-001"):
        call_command("node", "dream-mode-check", "--networks-on")


@pytest.mark.django_db
def test_node_dream_mode_check_json_blocks_other_nodes_network_on(monkeypatch):
    Node._local_cache.clear()
    node = Node.objects.create(
        hostname="terminal",
        public_endpoint="terminal",
        current_relation=Node.Relation.SELF,
    )
    monkeypatch.setattr(Node, "get_local", classmethod(lambda cls: node))
    monkeypatch.setattr(
        dream_mode.usb_inventory,
        "state_or_refresh",
        lambda *, refresh=False: {"devices": []},
    )

    stdout = StringIO()
    with pytest.raises(CommandError, match="restricted to gway-001"):
        call_command(
            "node",
            "dream-mode-check",
            "--networks-on",
            "--json",
            stdout=stdout,
        )

    payload = json.loads(stdout.getvalue())
    assert payload["allowed"] is False
    assert payload["blockers"] == ["network-on-not-allowed-for-node"]
