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


@pytest.mark.django_db
def test_usb_inventory_text_output_escapes_control_characters(
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
                {
                    "label": "EVIL\x1b]2;OWNED\x07\n\x9b31mspoofed",
                    "mountpoint": "/mnt/usb\x1b[31m\n\x9b32mFAKEPATH",
                    "claims": [
                        "camera",
                        "claim\x1b]2;CLAIM\x07\n\x9b33mFAKE-CLAIM",
                    ],
                }
            ]
        },
    )
    monkeypatch.setattr(
        usb_inventory,
        "claimed_paths",
        lambda *args, **kwargs: ["/mnt/usb\x1b[31m\n\x9b32mFAKEPATH"],
    )
    monkeypatch.setattr(
        usb_inventory,
        "path_claims",
        lambda *args, **kwargs: ["claim\x1b]2;CLAIM\x07\n\x9b33mFAKE-CLAIM"],
    )

    list_stdout = StringIO()
    call_command("sensors", "usb-inventory", "list", stdout=list_stdout)
    list_output = list_stdout.getvalue()
    assert "\\u001b" in list_output
    assert "\\u009b" in list_output
    assert "\\n" in list_output
    assert "\x1b" not in list_output
    assert "\x9b" not in list_output

    claimed_stdout = StringIO()
    call_command(
        "sensors",
        "usb-inventory",
        "claimed-path",
        "--role",
        "camera",
        stdout=claimed_stdout,
    )
    claimed_output = claimed_stdout.getvalue()
    assert "\\u001b" in claimed_output
    assert "\\u009b" in claimed_output
    assert "\\n" in claimed_output
    assert "\x1b" not in claimed_output
    assert "\x9b" not in claimed_output

    claims_stdout = StringIO()
    call_command(
        "sensors",
        "usb-inventory",
        "path-claims",
        "/dev/sda1",
        stdout=claims_stdout,
    )
    claims_output = claims_stdout.getvalue()
    assert "\\u001b" in claims_output
    assert "\\u009b" in claims_output
    assert "\\n" in claims_output
    assert "\x1b" not in claims_output
    assert "\x9b" not in claims_output
