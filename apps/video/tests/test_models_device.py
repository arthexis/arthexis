from __future__ import annotations

from apps.video.models import device as device_module
from apps.video.models.device import VideoDevice
from apps.video.utils import CameraStackProbe, Cv2CameraDevice


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
                identifier="0",
                description="OpenCV Camera 0",
                raw_info="device_index=0 backend=DSHOW frame_size=1280x720",
            )
        ],
    )

    devices = VideoDevice.detect_devices()

    assert len(devices) == 1
    assert devices[0].identifier == "0"
    assert devices[0].description == "OpenCV Camera 0"
    assert "backend=DSHOW" in devices[0].raw_info
