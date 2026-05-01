from __future__ import annotations

import pytest

from apps.nodes.models import Node
from apps.video.models import device as device_module
from apps.video.models.device import DetectedVideoDevice, VideoDevice
from apps.video.utils import CameraStackProbe, Cv2CameraDevice, cv2_camera_identifier


def test_detect_devices_skips_missing_camera_stack(monkeypatch):
    monkeypatch.setattr(
        device_module,
        "probe_rpi_camera_stack",
        lambda: CameraStackProbe(
            available=False,
            backend="missing",
            reason="No attached cameras detected",
        ),
    )
    monkeypatch.setattr(device_module, "detect_cv2_camera_devices", lambda: [])

    assert VideoDevice.detect_devices() == []


def test_detect_devices_describes_attached_rpicam(monkeypatch):
    monkeypatch.setattr(
        device_module,
        "probe_rpi_camera_stack",
        lambda: CameraStackProbe(
            available=True,
            backend="rpicam",
            reason="2 attached cameras detected",
            detected_cameras=2,
        ),
    )

    devices = VideoDevice.detect_devices()

    assert len(devices) == 1
    assert devices[0].identifier == str(device_module.RPI_CAMERA_DEVICE)
    assert devices[0].description == "Raspberry Pi Camera"
    assert "cameras=2" in devices[0].raw_info


def test_detect_devices_describes_ffmpeg_fallback(monkeypatch):
    monkeypatch.setattr(
        device_module,
        "probe_rpi_camera_stack",
        lambda: CameraStackProbe(
            available=True,
            backend="ffmpeg",
            reason="Video4Linux device /dev/video0 is available",
        ),
    )

    devices = VideoDevice.detect_devices()

    assert len(devices) == 1
    assert devices[0].description == "Video4Linux Camera"
    assert "backend=ffmpeg" in devices[0].raw_info


def test_detect_devices_describes_cv2_fallback(monkeypatch):
    monkeypatch.setattr(
        device_module,
        "probe_rpi_camera_stack",
        lambda: CameraStackProbe(
            available=False,
            backend="missing",
            reason="No attached cameras detected",
        ),
    )
    monkeypatch.setattr(
        device_module,
        "detect_cv2_camera_devices",
        lambda: [
            Cv2CameraDevice(
                identifier=cv2_camera_identifier(0),
                description="OpenCV Camera 0",
                raw_info="device_index=0 backend=DSHOW frame_size=1280x720",
            )
        ],
    )

    devices = VideoDevice.detect_devices()

    assert len(devices) == 1
    assert devices[0].identifier == "opencv:0"
    assert devices[0].description == "OpenCV Camera 0"
    assert "backend=DSHOW" in devices[0].raw_info


@pytest.mark.django_db
def test_refresh_from_system_updates_returned_default_state(monkeypatch):
    node = Node.objects.create(hostname="video-default", public_endpoint="video-default")
    monkeypatch.setattr(
        device_module,
        "is_feature_active_for_node",
        lambda *, node, slug: True,
    )
    monkeypatch.setattr(
        VideoDevice,
        "detect_devices",
        classmethod(
            lambda cls: [
                DetectedVideoDevice(
                    identifier="opencv:0",
                    description="OpenCV Camera 0",
                    raw_info="device_index=0 backend=FAKE frame_size=640x480",
                )
            ]
        ),
    )

    created, updated, created_objects, updated_objects = VideoDevice.refresh_from_system(
        node=node,
        return_objects=True,
    )

    assert (created, updated) == (1, 0)
    assert len(created_objects) == 1
    assert updated_objects == []
    assert created_objects[0].is_default is True


@pytest.mark.django_db
def test_ensure_single_default_clears_extra_defaults():
    node = Node.objects.create(
        hostname="video-extra-defaults",
        public_endpoint="video-extra-defaults",
    )
    VideoDevice.objects.create(node=node, identifier="opencv:0", is_default=True)
    extra = VideoDevice.objects.create(node=node, identifier="opencv:1", is_default=True)

    VideoDevice._ensure_single_default_for_node(node)

    extra.refresh_from_db()
    assert extra.is_default is False
