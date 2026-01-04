import logging
import os
import shutil
import stat
import subprocess
import threading
import uuid
from datetime import datetime
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

WORK_DIR = Path(settings.BASE_DIR) / "work"
CAMERA_DIR = WORK_DIR / "camera"
RPI_CAMERA_DEVICE = Path("/dev/video0")
RPI_CAMERA_BINARIES = ("rpicam-hello", "rpicam-still", "rpicam-vid")

_CAMERA_LOCK = threading.Lock()


def _camera_device_accessible(device: Path = RPI_CAMERA_DEVICE) -> bool:
    """Return ``True`` when the camera device node is usable."""

    if not device.exists():
        return False
    device_path = str(device)
    try:
        mode = os.stat(device_path).st_mode
    except OSError:
        return False
    if not stat.S_ISCHR(mode):
        return False
    if not os.access(device_path, os.R_OK | os.W_OK):
        return False
    return True


def _rpi_camera_binaries_ready(binaries: tuple[str, ...] = RPI_CAMERA_BINARIES) -> bool:
    """Return ``True`` when the Raspberry Pi camera binaries are callable."""

    for binary in binaries:
        tool_path = shutil.which(binary)
        if not tool_path:
            return False
        try:
            result = subprocess.run(
                [tool_path, "--help"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
        except Exception:
            return False
        if result.returncode != 0:
            return False
    return True


def _probe_rpi_camera_stack() -> bool:
    """Return ``True`` when the RPi camera stack reports at least one camera."""

    probe_tool = shutil.which("rpicam-hello") or shutil.which("libcamera-hello")
    if not probe_tool:
        return False

    try:
        result = subprocess.run(
            [probe_tool, "--list-cameras", "--nopreview", "-t", "1"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except Exception:  # pragma: no cover - depends on camera stack
        logger.warning("Failed to probe camera availability using %s", probe_tool)
        return False

    if result.returncode != 0:
        logger.warning(
            "Camera probe %s exited with %s: %s",
            probe_tool,
            result.returncode,
            (result.stderr or result.stdout or "").strip(),
        )
        return False

    output = (result.stdout or "") + (result.stderr or "")
    return "Available cameras" in output or bool(output.strip())


def has_rpi_camera_stack() -> bool:
    """Return ``True`` when the Raspberry Pi camera stack is available."""

    return (
        _camera_device_accessible()
        and _rpi_camera_binaries_ready()
        and _probe_rpi_camera_stack()
    )


def capture_rpi_snapshot(timeout: int = 10) -> Path:
    """Capture a snapshot using the Raspberry Pi camera stack."""

    tool_path = shutil.which("rpicam-still")
    if not tool_path:
        raise RuntimeError("rpicam-still is not available")
    CAMERA_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.utcnow()
    unique_suffix = uuid.uuid4().hex
    filename = CAMERA_DIR / f"{timestamp:%Y%m%d%H%M%S}-{unique_suffix}.jpg"
    acquired = _CAMERA_LOCK.acquire(timeout=timeout)
    if not acquired:
        raise RuntimeError("Camera is busy. Wait for the current capture to finish.")

    try:
        result = subprocess.run(
            [tool_path, "-o", str(filename), "-t", "1"],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except Exception as exc:  # pragma: no cover - depends on camera stack
        logger.error("Failed to invoke %s: %s", tool_path, exc)
        raise RuntimeError(f"Snapshot capture failed: {exc}") from exc
    finally:
        _CAMERA_LOCK.release()
    if result.returncode != 0:
        error = (result.stderr or result.stdout or "Snapshot capture failed").strip()
        logger.error("rpicam-still exited with %s: %s", result.returncode, error)
        raise RuntimeError(error)
    if not filename.exists():
        logger.error("Snapshot file %s was not created", filename)
        raise RuntimeError("Snapshot capture failed")
    return filename


def record_rpi_video(duration_seconds: int = 5, timeout: int = 15) -> Path:
    """Record a short video using the Raspberry Pi camera stack."""

    tool_path = shutil.which("rpicam-vid")
    if not tool_path:
        raise RuntimeError("rpicam-vid is not available")

    CAMERA_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.utcnow()
    unique_suffix = uuid.uuid4().hex
    filename = CAMERA_DIR / f"{timestamp:%Y%m%d%H%M%S}-{unique_suffix}.mp4"

    acquired = _CAMERA_LOCK.acquire(timeout=timeout)
    if not acquired:
        raise RuntimeError("Camera is busy. Wait for the current capture to finish.")

    try:
        result = subprocess.run(
            [
                tool_path,
                "-o",
                str(filename),
                "-t",
                str(max(1, duration_seconds * 1000)),
                "--codec",
                "libav",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except Exception as exc:  # pragma: no cover - depends on camera stack
        logger.error("Failed to invoke %s: %s", tool_path, exc)
        raise RuntimeError(f"Video capture failed: {exc}") from exc
    finally:
        _CAMERA_LOCK.release()
    if result.returncode != 0:
        error = (result.stderr or result.stdout or "Video capture failed").strip()
        logger.error("rpicam-vid exited with %s: %s", result.returncode, error)
        raise RuntimeError(error)
    if not filename.exists():
        logger.error("Video file %s was not created", filename)
        raise RuntimeError("Video capture failed")
    return filename


__all__ = [
    "CAMERA_DIR",
    "RPI_CAMERA_BINARIES",
    "RPI_CAMERA_DEVICE",
    "capture_rpi_snapshot",
    "has_rpi_camera_stack",
    "record_rpi_video",
]
