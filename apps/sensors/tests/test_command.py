from __future__ import annotations

from io import StringIO

import pytest
from django.core.management.base import CommandError
from django.core.management import call_command

from apps.nodes.models import Node
from apps.sensors.models import UsbPortMapping, UsbTracker

pytestmark = pytest.mark.django_db


def test_sensors_scan_usb_trackers_command_outputs_summary(settings, tmp_path):
    mount_root = tmp_path / "media"
    mount = mount_root / "usb-device"
    mount.mkdir(parents=True)
    match_file = mount / "data" / "marker.txt"
    match_file.parent.mkdir(parents=True)
    match_file.write_text("status=ready\n", encoding="utf-8")
    settings.USB_TRACKER_MOUNT_ROOTS = [str(mount_root)]
    UsbTracker.objects.create(
        name="Import drive",
        slug="import-drive",
        required_file_path="data/marker.txt",
        required_file_regex=r"status=ready",
    )

    output = StringIO()
    call_command("sensors", "scan-usb-trackers", stdout=output)

    assert "USB tracker scan complete" in output.getvalue()
    assert "matched=1" in output.getvalue()


def test_sensors_scan_usb_trackers_command_json_output(settings, tmp_path):
    mount_root = tmp_path / "media"
    mount_root.mkdir(parents=True)
    settings.USB_TRACKER_MOUNT_ROOTS = [str(mount_root)]
    UsbTracker.objects.create(
        name="Import drive",
        slug="import-drive",
        required_file_path="data/marker.txt",
    )

    output = StringIO()
    call_command("sensors", "scan-usb-trackers", "--json", stdout=output)

    assert output.getvalue().strip() == '{"failed": 0, "matched": 0, "scanned": 1}'


def test_sensors_set_usb_lcd_port_command_configures_mapping(settings, tmp_path):
    settings.BASE_DIR = tmp_path
    Node._local_cache.clear()
    node = Node.objects.create(hostname="gway", public_endpoint="gway")
    Node.objects.filter(pk=node.pk).update(current_relation=Node.Relation.SELF)
    output = StringIO()

    call_command(
        "sensors",
        "set-usb-lcd-port",
        "--port",
        "1",
        "--source-type",
        "usb-tracker",
        "--source-id",
        "usb-key",
        "--label",
        "BASTION-KEY",
        stdout=output,
    )

    mapping = UsbPortMapping.objects.get(node=node, port_number=1)
    assert mapping.source_type == UsbPortMapping.SourceType.USB_TRACKER
    assert mapping.source_identifier == "usb-key"
    assert mapping.label == "BASTION"
    assert "USB LCD port 1 created" in output.getvalue()


def test_sensors_write_usb_lcd_status_command_outputs_summary(settings, tmp_path):
    settings.BASE_DIR = tmp_path
    Node._local_cache.clear()
    node = Node.objects.create(hostname="gway", public_endpoint="gway")
    Node.objects.filter(pk=node.pk).update(current_relation=Node.Relation.SELF)
    UsbTracker.objects.create(
        name="Bastion drive",
        slug="bastion",
        required_file_path="security/key.txt",
        last_match_path="/media/bastion/security/key.txt",
    )
    UsbPortMapping.objects.create(
        node=node,
        port_number=1,
        label="BASTION",
        source_type=UsbPortMapping.SourceType.USB_TRACKER,
        source_identifier="bastion",
    )

    output = StringIO()
    call_command("sensors", "write-usb-lcd-status", stdout=output)

    assert "USB LCD status written" in output.getvalue()
    assert "configured=1 connected=1" in output.getvalue()


def test_sensors_clear_usb_lcd_port_command_removes_mapping(settings, tmp_path):
    settings.BASE_DIR = tmp_path
    Node._local_cache.clear()
    node = Node.objects.create(hostname="gway", public_endpoint="gway")
    other_node = Node.objects.create(hostname="remote", public_endpoint="remote")
    Node.objects.filter(pk=node.pk).update(current_relation=Node.Relation.SELF)
    UsbPortMapping.objects.create(
        node=node,
        port_number=1,
        label="BASTION",
        source_type=UsbPortMapping.SourceType.USB_TRACKER,
        source_identifier="bastion",
    )
    UsbPortMapping.objects.create(
        node=other_node,
        port_number=1,
        label="REMOTE",
        source_type=UsbPortMapping.SourceType.USB_TRACKER,
        source_identifier="remote",
    )

    output = StringIO()
    call_command("sensors", "clear-usb-lcd-port", "--port", "1", stdout=output)

    assert not UsbPortMapping.objects.filter(node=node, port_number=1).exists()
    assert UsbPortMapping.objects.filter(node=other_node, port_number=1).exists()
    assert "USB LCD port 1 cleared" in output.getvalue()


def test_sensors_set_usb_lcd_port_requires_local_node(settings, tmp_path):
    settings.BASE_DIR = tmp_path
    Node._local_cache.clear()

    with pytest.raises(CommandError, match="No local node is registered"):
        call_command(
            "sensors",
            "set-usb-lcd-port",
            "--port",
            "1",
            "--source-type",
            "usb-tracker",
            "--source-id",
            "usb-key",
        )
