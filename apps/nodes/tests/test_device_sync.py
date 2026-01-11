from __future__ import annotations

from dataclasses import dataclass

import pytest

from apps.audio.models import RecordingDevice
from apps.nodes.device_sync import sync_detected_devices
from apps.nodes.models import Node


@dataclass(frozen=True)
class DetectedDevice:
    identifier: str
    description: str
    capture_channels: int
    raw_info: str


def _sync_recording_devices(node: Node, detected: list[DetectedDevice]) -> tuple[int, int]:
    return sync_detected_devices(
        model_cls=RecordingDevice,
        node=node,
        detected=detected,
        identifier_getter=lambda device: device.identifier,
        defaults_getter=lambda device: {
            "description": device.description,
            "capture_channels": device.capture_channels,
            "raw_info": device.raw_info,
        },
    )


@pytest.mark.django_db
def test_sync_detected_devices_creates_rows():
    node = Node.objects.create(hostname="sync-create", public_endpoint="sync-create")
    detected = [
        DetectedDevice(
            identifier="mic-1",
            description="USB mic",
            capture_channels=2,
            raw_info="mic-1: USB mic capture 2",
        ),
        DetectedDevice(
            identifier="mic-2",
            description="Built-in mic",
            capture_channels=1,
            raw_info="mic-2: Built-in mic capture 1",
        ),
    ]

    created, updated = _sync_recording_devices(node, detected)

    assert created == 2
    assert updated == 0
    assert RecordingDevice.objects.filter(node=node).count() == 2


@pytest.mark.django_db
def test_sync_detected_devices_updates_only_changed_fields():
    node = Node.objects.create(hostname="sync-update", public_endpoint="sync-update")
    device = RecordingDevice.objects.create(
        node=node,
        identifier="mic-1",
        description="Old mic",
        capture_channels=1,
        raw_info="old",
    )
    detected = [
        DetectedDevice(
            identifier="mic-1",
            description="New mic",
            capture_channels=1,
            raw_info="old",
        )
    ]

    created, updated = _sync_recording_devices(node, detected)

    assert created == 0
    assert updated == 1
    device.refresh_from_db()
    assert device.description == "New mic"
    assert device.capture_channels == 1
    assert device.raw_info == "old"


@pytest.mark.django_db
def test_sync_detected_devices_skips_updates_when_unchanged_and_deletes_missing():
    node = Node.objects.create(hostname="sync-delete", public_endpoint="sync-delete")
    RecordingDevice.objects.create(
        node=node,
        identifier="mic-1",
        description="Same mic",
        capture_channels=2,
        raw_info="same",
    )
    RecordingDevice.objects.create(
        node=node,
        identifier="mic-2",
        description="Extra mic",
        capture_channels=1,
        raw_info="extra",
    )
    detected = [
        DetectedDevice(
            identifier="mic-1",
            description="Same mic",
            capture_channels=2,
            raw_info="same",
        )
    ]

    created, updated = _sync_recording_devices(node, detected)

    assert created == 0
    assert updated == 0
    remaining = RecordingDevice.objects.filter(node=node)
    assert remaining.count() == 1
    assert remaining.get().identifier == "mic-1"

    created, updated = _sync_recording_devices(node, [])

    assert created == 0
    assert updated == 0
    assert RecordingDevice.objects.filter(node=node).count() == 0
