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


def test_usb_inventory_excludes_pi_image_media_from_usb_consumer_claims(
    settings, monkeypatch, tmp_path
):
    boot_mount = tmp_path / "boot"
    root_mount = tmp_path / "root"
    settings.USB_INVENTORY_CLAIMS_PATH = tmp_path / "claims.json"
    settings.USB_INVENTORY_STATE_PATH = tmp_path / "devices.json"
    settings.USB_INVENTORY_CLAIMS_PATH.write_text(
        json.dumps(
            {
                "version": 1,
                "claims": [
                    {
                        "role": "kindle-postbox",
                        "match": {"tran": "usb"},
                    },
                    {
                        "role": "kindle-postbox",
                        "match": {"fstype": "vfat"},
                    },
                    {
                        "role": "bastion-unlock",
                        "match": {"fstype": "ext4"},
                    },
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
                        "children": [
                            {
                                "name": "sda1",
                                "path": "/dev/sda1",
                                "type": "part",
                                "label": "bootfs",
                                "fstype": "vfat",
                            },
                            {
                                "name": "sda2",
                                "path": "/dev/sda2",
                                "type": "part",
                                "label": "rootfs",
                                "fstype": "ext4",
                            },
                        ],
                    }
                ]
            }
        return {
            "filesystems": [
                {
                    "source": "/dev/sda1",
                    "target": str(boot_mount),
                    "fstype": "vfat",
                    "options": "rw",
                },
                {
                    "source": "/dev/sda2",
                    "target": str(root_mount),
                    "fstype": "ext4",
                    "options": "rw",
                },
            ]
        }

    monkeypatch.setattr(usb_inventory, "run_json", fake_run_json)

    payload = usb_inventory.refresh_inventory()
    by_path = {device["path"]: device for device in payload["devices"]}

    assert by_path["/dev/sda"]["pi_image"] is True
    assert by_path["/dev/sda"]["imager_media"] is True
    assert by_path["/dev/sda"]["claims"] == []
    assert by_path["/dev/sda1"]["pi_image"] is True
    assert by_path["/dev/sda1"]["imager_media"] is True
    assert by_path["/dev/sda1"]["claims"] == []
    assert by_path["/dev/sda2"]["pi_image"] is True
    assert by_path["/dev/sda2"]["imager_media"] is True
    assert by_path["/dev/sda2"]["claims"] == []
    assert usb_inventory.claimed_paths("kindle-postbox") == []
    assert usb_inventory.claimed_paths("bastion-unlock") == []


def test_usb_inventory_refreshes_configured_burner_symlink_tokens(
    settings, monkeypatch, tmp_path
):
    burner_link = tmp_path / "by-id" / "usb-FNK_SD_burner"
    burner_link.parent.mkdir()
    data_mount = tmp_path / "data"
    settings.USB_INVENTORY_CLAIMS_PATH = tmp_path / "claims.json"
    settings.USB_INVENTORY_STATE_PATH = tmp_path / "devices.json"
    settings.USB_INVENTORY_CLAIMS_PATH.write_text(
        json.dumps(
            {
                "version": 1,
                "claims": [
                    {
                        "role": "kindle-postbox",
                        "match": {"fstype": "vfat"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("IMAGER_BURN_DEVICE", str(burner_link))

    def fake_run_json(command):
        if command[0] == "lsblk":
            return {
                "blockdevices": [
                    {
                        "name": "sdz",
                        "path": "/dev/sdz",
                        "type": "disk",
                        "tran": "usb",
                        "children": [
                            {
                                "name": "sdz1",
                                "path": "/dev/sdz1",
                                "type": "part",
                                "label": "DATA",
                                "fstype": "vfat",
                            }
                        ],
                    }
                ]
            }
        return {
            "filesystems": [
                {
                    "source": "/dev/sdz1",
                    "target": str(data_mount),
                    "fstype": "vfat",
                    "options": "rw",
                }
            ]
        }

    monkeypatch.setattr(usb_inventory, "run_json", fake_run_json)

    tokens_before_link_exists = usb_inventory._configured_imager_burner_tokens()
    burner_link.symlink_to("/dev/sdz")
    payload = usb_inventory.refresh_inventory()
    by_path = {device["path"]: device for device in payload["devices"]}

    assert "/dev/sdz" not in tokens_before_link_exists
    assert by_path["/dev/sdz"]["configured_imager_burner"] is True
    assert by_path["/dev/sdz1"]["configured_imager_burner"] is True
    assert by_path["/dev/sdz1"]["imager_media"] is True
    assert by_path["/dev/sdz1"]["claims"] == []
    assert usb_inventory.claimed_paths("kindle-postbox") == []


def test_usb_inventory_reuses_configured_burner_tokens_within_refresh(
    settings, monkeypatch, tmp_path
):
    data_mount = tmp_path / "data"
    settings.USB_INVENTORY_CLAIMS_PATH = tmp_path / "claims.json"
    settings.USB_INVENTORY_STATE_PATH = tmp_path / "devices.json"
    settings.USB_INVENTORY_CLAIMS_PATH.write_text(
        json.dumps(
            {
                "version": 1,
                "claims": [
                    {
                        "role": "kindle-postbox",
                        "match": {"fstype": "vfat"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    token_calls = 0

    def fake_configured_tokens():
        nonlocal token_calls
        token_calls += 1
        return {"/dev/sdz", "sdz"}

    def fake_run_json(command):
        if command[0] == "lsblk":
            return {
                "blockdevices": [
                    {
                        "name": "sdz",
                        "path": "/dev/sdz",
                        "type": "disk",
                        "tran": "usb",
                        "children": [
                            {
                                "name": "sdz1",
                                "path": "/dev/sdz1",
                                "type": "part",
                                "label": "DATA",
                                "fstype": "vfat",
                            }
                        ],
                    }
                ]
            }
        return {
            "filesystems": [
                {
                    "source": "/dev/sdz1",
                    "target": str(data_mount),
                    "fstype": "vfat",
                    "options": "rw",
                }
            ]
        }

    monkeypatch.setattr(
        usb_inventory,
        "_configured_imager_burner_tokens",
        fake_configured_tokens,
    )
    monkeypatch.setattr(usb_inventory, "run_json", fake_run_json)

    payload = usb_inventory.refresh_inventory()
    by_path = {device["path"]: device for device in payload["devices"]}

    assert token_calls == 1
    assert by_path["/dev/sdz"]["configured_imager_burner"] is True
    assert by_path["/dev/sdz1"]["configured_imager_burner"] is True
    assert by_path["/dev/sdz1"]["claims"] == []


def test_usb_inventory_excludes_configured_burner_partitions_from_usb_claims(
    settings, monkeypatch, tmp_path
):
    burner_link = tmp_path / "by-id" / "usb-FNK_SD_burner"
    burner_link.parent.mkdir()
    burner_link.symlink_to("/dev/sdz")
    data_mount = tmp_path / "data"
    settings.USB_INVENTORY_CLAIMS_PATH = tmp_path / "claims.json"
    settings.USB_INVENTORY_STATE_PATH = tmp_path / "devices.json"
    settings.USB_INVENTORY_CLAIMS_PATH.write_text(
        json.dumps(
            {
                "version": 1,
                "claims": [
                    {
                        "role": "kindle-postbox",
                        "match": {"fstype": "vfat"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("IMAGER_BURN_DEVICE", str(burner_link))

    def fake_run_json(command):
        if command[0] == "lsblk":
            return {
                "blockdevices": [
                    {
                        "name": "sdz",
                        "path": "/dev/sdz",
                        "type": "disk",
                        "tran": "usb",
                        "children": [
                            {
                                "name": "sdz1",
                                "path": "/dev/sdz1",
                                "type": "part",
                                "label": "DATA",
                                "fstype": "vfat",
                            }
                        ],
                    }
                ]
            }
        return {
            "filesystems": [
                {
                    "source": "/dev/sdz1",
                    "target": str(data_mount),
                    "fstype": "vfat",
                    "options": "rw",
                }
            ]
        }

    monkeypatch.setattr(usb_inventory, "run_json", fake_run_json)

    payload = usb_inventory.refresh_inventory()
    by_path = {device["path"]: device for device in payload["devices"]}

    assert by_path["/dev/sdz"]["configured_imager_burner"] is True
    assert by_path["/dev/sdz1"]["configured_imager_burner"] is True
    assert by_path["/dev/sdz1"]["imager_media"] is True
    assert by_path["/dev/sdz1"]["claims"] == []
    assert usb_inventory.claimed_paths("kindle-postbox") == []


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


def test_usb_inventory_treats_state_strings_as_single_values(settings, tmp_path):
    kindle_mount = tmp_path / "kindle"
    unrelated = tmp_path / "unrelated"
    state_path = tmp_path / "devices.json"
    settings.USB_INVENTORY_STATE_PATH = state_path
    kindle_mount.mkdir()
    unrelated.mkdir()
    state_path.write_text(
        json.dumps(
            {
                "devices": [
                    {
                        "claimed_roles": ["kindle-postbox"],
                        "claims": "kindle-postbox",
                        "mountpoints": str(kindle_mount),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    assert usb_inventory.claimed_paths("kindle-postbox") == [str(kindle_mount)]
    assert usb_inventory.path_claims(kindle_mount / "documents") == ["kindle-postbox"]
    assert usb_inventory.path_claims(unrelated) == []


def test_usb_inventory_reads_dict_claims_and_mounts(settings, tmp_path):
    kindle_mount = tmp_path / "kindle"
    state_path = tmp_path / "devices.json"
    settings.USB_INVENTORY_STATE_PATH = state_path
    state_path.write_text(
        json.dumps(
            {
                "devices": [
                    {
                        "claims": {"kindle-postbox": {"owner": "kindle-postbox"}},
                        "mounts": {"main": {"target": str(kindle_mount)}},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    assert usb_inventory.claimed_paths("kindle-postbox") == [str(kindle_mount)]


def test_usb_inventory_path_claims_ignores_broad_state_roots(settings, tmp_path):
    unrelated = tmp_path / "unrelated"
    state_path = tmp_path / "devices.json"
    settings.USB_INVENTORY_STATE_PATH = state_path
    unrelated.mkdir()
    state_path.write_text(
        json.dumps(
            {
                "devices": [
                    {
                        "claimed_roles": ["kindle-postbox"],
                        "mountpoints": [str(tmp_path.anchor)],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    assert usb_inventory.path_claims(unrelated) == []


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
                    "label": "EVIL\x7f\x1b]2;OWNED\x07\n\x9b31mspoofed",
                    "mountpoint": "/mnt/usb\x7f\x1b[31m\n\x9b32mFAKEPATH",
                    "claims": [
                        "camera",
                        "claim\x7f\x1b]2;CLAIM\x07\n\x9b33mFAKE-CLAIM",
                    ],
                }
            ]
        },
    )
    monkeypatch.setattr(
        usb_inventory,
        "claimed_paths",
        lambda *args, **kwargs: ["/mnt/usb\x7f\x1b[31m\n\x9b32mFAKEPATH"],
    )
    monkeypatch.setattr(
        usb_inventory,
        "path_claims",
        lambda *args, **kwargs: ["claim\x7f\x1b]2;CLAIM\x07\n\x9b33mFAKE-CLAIM"],
    )

    list_stdout = StringIO()
    call_command("sensors", "usb-inventory", "list", stdout=list_stdout)
    list_output = list_stdout.getvalue()
    assert "\\u001b" in list_output
    assert "\\u007f" in list_output
    assert "\\u009b" in list_output
    assert "\\n" in list_output
    assert "\x1b" not in list_output
    assert "\x7f" not in list_output
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
    assert "\\u007f" in claimed_output
    assert "\\u009b" in claimed_output
    assert "\\n" in claimed_output
    assert "\x1b" not in claimed_output
    assert "\x7f" not in claimed_output
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
    assert "\\u007f" in claims_output
    assert "\\u009b" in claims_output
    assert "\\n" in claims_output
    assert "\x1b" not in claims_output
    assert "\x7f" not in claims_output
    assert "\x9b" not in claims_output
