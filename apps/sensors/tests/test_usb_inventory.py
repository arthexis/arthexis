from __future__ import annotations

import json
import subprocess
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.nodes.models import Node, NodeRole
from apps.sensors import node_features, usb_inventory


def test_usb_inventory_matches_kindle_claim(settings, monkeypatch, tmp_path):
    mount = tmp_path / "kindle"
    (mount / "documents").mkdir(parents=True)
    (mount / "system").mkdir()
    settings.USB_INVENTORY_CLAIMS_PATH = tmp_path / "claims.json"
    settings.USB_INVENTORY_STATE_PATH = tmp_path / "devices.json"
    settings.USB_INVENTORY_CLAIMS_PATH.write_text(
        json.dumps({"kindle-postbox": {"match": {"kindle": True, "label": "Kindle"}}}),
        encoding="utf-8",
    )

    def fake_run_json(command):
        if command[0] == "lsblk":
            return {
                "blockdevices": [
                    {
                        "name": "sda",
                        "path": "/dev/sda",
                        "type": "disk",
                        "tran": "usb",
                        "children": [
                            {
                                "name": "sda1",
                                "path": "/dev/sda1",
                                "type": "part",
                                "label": "Kindle",
                            }
                        ],
                    }
                ]
            }
        return {
            "filesystems": [
                {
                    "source": "/dev/sda1",
                    "target": str(mount),
                    "fstype": "vfat",
                    "options": "rw",
                }
            ]
        }

    monkeypatch.setattr(usb_inventory, "run_json", fake_run_json)

    payload = usb_inventory.refresh_inventory()

    assert payload["devices"][1]["claims"] == ["kindle-postbox"]
    assert payload["devices"][1]["kindle_shape"] is True
    assert usb_inventory.claimed_paths("kindle-postbox") == [str(mount)]


def test_usb_inventory_matches_live_kindle_shape_claim_alias(settings, monkeypatch, tmp_path):
    key_mount = tmp_path / "bastion"
    kindle_mount = tmp_path / "kindle"
    key_mount.mkdir()
    (kindle_mount / "documents").mkdir(parents=True)
    (kindle_mount / "system").mkdir()
    settings.USB_INVENTORY_CLAIMS_PATH = tmp_path / "claims.json"
    settings.USB_INVENTORY_STATE_PATH = tmp_path / "devices.json"
    settings.USB_INVENTORY_CLAIMS_PATH.write_text(
        json.dumps(
            {
                "version": 1,
                "claims": [
                    {
                        "id": "kindle-postbox",
                        "role": "kindle-postbox",
                        "match": {"kindle_shape": True},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    def fake_run_json(command):
        if command[0] == "lsblk":
            return {
                "blockdevices": [
                    {
                        "name": "sda",
                        "path": "/dev/sda",
                        "type": "disk",
                        "tran": "usb",
                        "label": "ESD-USB",
                        "mountpoint": str(key_mount),
                    },
                    {
                        "name": "sdb",
                        "path": "/dev/sdb",
                        "type": "disk",
                        "tran": "usb",
                        "label": "Kindle",
                        "mountpoint": str(kindle_mount),
                    },
                ]
            }
        return {"filesystems": []}

    monkeypatch.setattr(usb_inventory, "run_json", fake_run_json)

    payload = usb_inventory.refresh_inventory()

    assert payload["devices"][0]["claims"] == []
    assert payload["devices"][1]["claims"] == ["kindle-postbox"]
    assert usb_inventory.claimed_paths("kindle-postbox") == [str(kindle_mount)]


def test_usb_inventory_reads_service_generated_claim_state(settings, tmp_path):
    kindle_mount = tmp_path / "kindle"
    state_path = tmp_path / "devices.json"
    settings.USB_INVENTORY_STATE_PATH = state_path
    state_path.write_text(
        json.dumps(
            {
                "devices": [
                    {
                        "path": "/dev/sda1",
                        "claimed_roles": ["bastion-unlock"],
                        "claims": [{"role": "bastion-unlock"}],
                        "mountpoints": [str(tmp_path / "bastion")],
                    },
                    {
                        "path": "/dev/sdb",
                        "claimed_roles": ["kindle-postbox"],
                        "claims": [
                            {
                                "id": "kindle-postbox",
                                "role": "kindle-postbox",
                                "owner": "kindle-postbox",
                            }
                        ],
                        "mountpoints": [str(kindle_mount)],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    assert usb_inventory.claimed_paths("kindle-postbox") == [str(kindle_mount)]
    assert usb_inventory.path_claims(kindle_mount / "documents") == ["kindle-postbox"]


def test_atomic_write_json_cleans_temp_file_on_failure(tmp_path):
    target = tmp_path / "devices.json"

    with pytest.raises(TypeError):
        usb_inventory.atomic_write_json(target, {"bad": object()})

    assert not target.exists()
    assert list(tmp_path.iterdir()) == []


def test_run_json_raises_inventory_error_on_timeout(monkeypatch):
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs["timeout"])

    monkeypatch.setattr(usb_inventory.subprocess, "run", fake_run)

    with pytest.raises(usb_inventory.UsbInventoryError, match="timed out"):
        usb_inventory.run_json(["lsblk"])


@pytest.mark.django_db
def test_usb_inventory_feature_detection_requires_control_role(monkeypatch, tmp_path):
    control = NodeRole.objects.create(name="Control")
    terminal = NodeRole.objects.create(name="Terminal")
    node = Node(hostname="control", public_endpoint="control", role=control)
    monkeypatch.setattr(usb_inventory, "has_usb_inventory_tools", lambda: True)

    assert (
        node_features.check_node_feature(
            "usb-inventory",
            node=node,
            base_dir=tmp_path,
            base_path=tmp_path,
        )
        is True
    )

    node.role = terminal

    assert (
        node_features.check_node_feature(
            "usb-inventory",
            node=node,
            base_dir=tmp_path,
            base_path=tmp_path,
        )
        is False
    )


@pytest.mark.django_db
def test_usb_inventory_command_requires_control_role(settings, tmp_path):
    settings.BASE_DIR = tmp_path
    Node._local_cache.clear()
    node = Node.objects.create(hostname="terminal", public_endpoint="terminal")
    Node.objects.filter(pk=node.pk).update(current_relation=Node.Relation.SELF)

    with pytest.raises(CommandError, match="only available on Control nodes"):
        call_command("sensors", "usb-inventory", "list")


@pytest.mark.django_db
def test_usb_inventory_command_refreshes_for_control_role(
    settings, monkeypatch, tmp_path
):
    settings.BASE_DIR = tmp_path
    settings.USB_INVENTORY_STATE_PATH = tmp_path / "devices.json"
    Node._local_cache.clear()
    role = NodeRole.objects.create(name="Control")
    node = Node.objects.create(hostname="gway", public_endpoint="gway", role=role)
    Node.objects.filter(pk=node.pk).update(current_relation=Node.Relation.SELF)
    monkeypatch.setattr(usb_inventory, "has_usb_inventory_tools", lambda: True)
    monkeypatch.setattr(
        usb_inventory,
        "refresh_inventory",
        lambda: {"generated_at": "now", "devices": [{"name": "sda"}]},
    )

    output = StringIO()
    call_command("sensors", "usb-inventory", "refresh", stdout=output)

    assert "USB inventory refreshed: devices=1" in output.getvalue()


@pytest.mark.django_db
def test_usb_inventory_list_skips_malformed_state_entries(
    settings, monkeypatch, tmp_path
):
    settings.BASE_DIR = tmp_path
    Node._local_cache.clear()
    role = NodeRole.objects.create(name="Control")
    node = Node.objects.create(hostname="gway", public_endpoint="gway", role=role)
    Node.objects.filter(pk=node.pk).update(current_relation=Node.Relation.SELF)
    monkeypatch.setattr(usb_inventory, "has_usb_inventory_tools", lambda: True)
    monkeypatch.setattr(
        usb_inventory,
        "state_or_refresh",
        lambda *, refresh=False: {
            "devices": [
                "bad-entry",
                {"name": "sda1", "mountpoint": "/media/kindle", "claims": [123]},
            ]
        },
    )

    stdout = StringIO()
    stderr = StringIO()
    call_command("sensors", "usb-inventory", "list", stdout=stdout, stderr=stderr)

    assert "sda1 /media/kindle claims=123" in stdout.getvalue()
    assert "Skipping malformed USB inventory entry." in stderr.getvalue()
