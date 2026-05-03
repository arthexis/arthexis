from __future__ import annotations

from pathlib import Path

import pytest

from apps.audio.models import RecordingDevice
from apps.nodes.models import Node
from apps.screens.startup_notifications import LCD_USB_LOCK_FILE, read_lcd_lock_file
from apps.sensors.models import UsbPortMapping, UsbTracker
from apps.sensors.usb_lcd import (
    UsbPortStatus,
    build_usb_lcd_statuses,
    render_usb_lcd_lines,
    write_usb_lcd_status,
)
from apps.video.models import VideoDevice

pytestmark = pytest.mark.django_db


def test_render_usb_lcd_lines_fits_four_fixed_icon_slots() -> None:
    statuses = [
        UsbPortStatus(port_number=1, label="LISTEN", connected=True),
        UsbPortStatus(port_number=2, label="OBSERVE", connected=True),
        UsbPortStatus(port_number=3, label="BASTION", connected=True),
        UsbPortStatus(port_number=4, label="USB-KEY-LONG", connected=True),
    ]

    line1, line2 = render_usb_lcd_lines(statuses)

    assert (line1, line2) == ("▘LISTEN ▝OBSERVE", "▖BASTION▗USB-KEY")
    assert len(line1) == 16
    assert len(line2) == 16


def test_render_usb_lcd_lines_keeps_port_positions_for_sparse_statuses() -> None:
    line1, line2 = render_usb_lcd_lines(
        [
            UsbPortStatus(port_number=2, label="SPARE", connected=False),
            UsbPortStatus(port_number=4, label="CAM", connected=True),
        ]
    )

    assert line1 == "▘FREE   ▝FREE   "
    assert line2 == "▖FREE   ▗CAM    "


def test_build_usb_lcd_statuses_uses_local_mappings_and_default_labels() -> None:
    node = Node.objects.create(hostname="gway", public_endpoint="gway")
    other_node = Node.objects.create(hostname="remote", public_endpoint="remote")
    UsbTracker.objects.create(
        name="USB-KEY",
        slug="usb-key",
        required_file_path="security/key.txt",
        last_match_path="/media/USB-KEY/security/key.txt",
    )
    RecordingDevice.objects.create(
        node=node,
        identifier="1-0",
        description="USB Microphone",
        capture_channels=1,
    )
    VideoDevice.objects.create(
        node=node,
        identifier="opencv:0",
        name="USB Camera",
        description="USB camera",
    )
    UsbPortMapping.objects.create(
        node=other_node,
        port_number=1,
        label="REMOTE",
        source_type=UsbPortMapping.SourceType.USB_TRACKER,
        source_identifier="usb-key",
    )
    UsbPortMapping.objects.create(
        node=node,
        port_number=1,
        source_type=UsbPortMapping.SourceType.USB_TRACKER,
        source_identifier="usb-key",
    )
    UsbPortMapping.objects.create(
        node=node,
        port_number=2,
        source_type=UsbPortMapping.SourceType.RECORDING_DEVICE,
        source_identifier="1-0",
    )
    UsbPortMapping.objects.create(
        node=node,
        port_number=3,
        source_type=UsbPortMapping.SourceType.VIDEO_DEVICE,
        source_identifier="opencv:0",
    )
    UsbPortMapping.objects.create(
        node=node,
        port_number=4,
        source_type=UsbPortMapping.SourceType.USB_TRACKER,
        source_identifier="missing",
        label="SPARE",
    )

    statuses = build_usb_lcd_statuses(node=node)

    assert [status.label for status in statuses] == [
        "BASTION",
        "LISTEN",
        "OBSERVE",
        "FREE",
    ]
    assert [status.connected for status in statuses] == [True, True, True, False]


def test_node_scoped_devices_fail_closed_without_local_node(monkeypatch) -> None:
    monkeypatch.setattr("apps.sensors.usb_lcd._local_node", lambda: None)
    node = Node.objects.create(hostname="other", public_endpoint="other")
    RecordingDevice.objects.create(
        node=node,
        identifier="1-0",
        description="USB Microphone",
        capture_channels=1,
    )
    VideoDevice.objects.create(
        node=node,
        identifier="opencv:0",
        name="USB Camera",
        description="USB camera",
    )
    mappings = [
        UsbPortMapping.objects.create(
            node=node,
            port_number=1,
            source_type=UsbPortMapping.SourceType.RECORDING_DEVICE,
            source_identifier="1-0",
        ),
        UsbPortMapping.objects.create(
            node=node,
            port_number=2,
            source_type=UsbPortMapping.SourceType.VIDEO_DEVICE,
            source_identifier="opencv:0",
        ),
    ]

    statuses = build_usb_lcd_statuses(mappings=mappings)

    assert [status.connected for status in statuses[:2]] == [False, False]
    assert [status.label for status in statuses[:2]] == ["FREE", "FREE"]


def test_build_usb_lcd_statuses_ignores_other_node_mappings() -> None:
    local = Node.objects.create(hostname="local", public_endpoint="local")
    remote = Node.objects.create(hostname="remote", public_endpoint="remote")
    UsbTracker.objects.create(
        name="Bastion drive",
        slug="bastion",
        required_file_path="security/key.txt",
        last_match_path="/media/bastion/security/key.txt",
    )
    UsbPortMapping.objects.create(
        node=remote,
        port_number=1,
        label="REMOTE",
        source_type=UsbPortMapping.SourceType.USB_TRACKER,
        source_identifier="bastion",
    )

    statuses = build_usb_lcd_statuses(node=local)

    assert [status.label for status in statuses] == ["FREE", "FREE", "FREE", "FREE"]


def test_build_usb_lcd_statuses_filters_explicit_mappings_by_node() -> None:
    local = Node.objects.create(hostname="local", public_endpoint="local")
    remote = Node.objects.create(hostname="remote", public_endpoint="remote")
    UsbTracker.objects.create(
        name="Bastion drive",
        slug="bastion",
        required_file_path="security/key.txt",
        last_match_path="/media/bastion/security/key.txt",
    )
    local_mapping = UsbPortMapping.objects.create(
        node=local,
        port_number=1,
        label="LOCAL",
        source_type=UsbPortMapping.SourceType.USB_TRACKER,
        source_identifier="bastion",
    )
    remote_mapping = UsbPortMapping.objects.create(
        node=remote,
        port_number=2,
        label="REMOTE",
        source_type=UsbPortMapping.SourceType.USB_TRACKER,
        source_identifier="bastion",
    )

    statuses = build_usb_lcd_statuses(
        mappings=[local_mapping, remote_mapping], node=local
    )

    assert [status.label for status in statuses] == ["LOCAL", "FREE", "FREE", "FREE"]


def test_write_usb_lcd_status_writes_lock_file(tmp_path: Path) -> None:
    node = Node.objects.create(hostname="gway", public_endpoint="gway")
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

    result = write_usb_lcd_status(lock_dir=tmp_path, node=node)
    message = read_lcd_lock_file(tmp_path / LCD_USB_LOCK_FILE)

    assert result["written"] is True
    assert result["connected"] == 1
    assert result["line1"] == "▘BASTION▝FREE   "
    assert result["line2"] == "▖FREE   ▗FREE   "
    assert message is not None
    assert message.subject == "▘BASTION▝FREE"
    assert message.body == "▖FREE   ▗FREE"


def test_write_usb_lcd_status_removes_stale_lock_without_mappings(
    tmp_path: Path,
) -> None:
    lock_file = tmp_path / LCD_USB_LOCK_FILE
    lock_file.write_text("old\npayload\n", encoding="utf-8")

    result = write_usb_lcd_status(lock_dir=tmp_path)

    assert result["written"] is False
    assert not lock_file.exists()
