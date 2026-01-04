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


def _is_video_device_available(device: Path) -> bool:
    """Return ``True`` when ``device`` exists and is a readable char device."""

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


def has_rpicam_binaries() -> bool:
    """Return ``True`` when the Raspberry Pi camera binaries are available."""

    device = RPI_CAMERA_DEVICE
    if not _is_video_device_available(device):
        return False
    for binary in RPI_CAMERA_BINARIES:
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


def _has_ffmpeg_capture_support() -> bool:
    """Return ``True`` when a generic V4L2 device can be captured with ffmpeg."""

    if not _is_video_device_available(RPI_CAMERA_DEVICE):
        return False
    return shutil.which("ffmpeg") is not None


def has_rpi_camera_stack() -> bool:
    """Return ``True`` when any supported camera stack is available."""

    return has_rpicam_binaries() or _has_ffmpeg_capture_support()


def capture_rpi_snapshot(timeout: int = 10) -> Path:
    """Capture a snapshot using the Raspberry Pi camera stack."""

    CAMERA_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.utcnow()
    unique_suffix = uuid.uuid4().hex
    filename = CAMERA_DIR / f"{timestamp:%Y%m%d%H%M%S}-{unique_suffix}.jpg"
    acquired = _CAMERA_LOCK.acquire(timeout=timeout)
    if not acquired:
        raise RuntimeError("Camera is busy. Wait for the current capture to finish.")

    try:
        if has_rpicam_binaries():
            tool_path = shutil.which("rpicam-still")
            if not tool_path:
                raise RuntimeError("rpicam-still is not available")
            command = [tool_path, "-o", str(filename), "-t", "1"]
        elif _has_ffmpeg_capture_support():
            tool_path = shutil.which("ffmpeg")
            if not tool_path:
                raise RuntimeError("ffmpeg is not available")
            command = [
                tool_path,
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "v4l2",
                "-i",
                str(RPI_CAMERA_DEVICE),
                "-frames:v",
                "1",
                "-y",
                str(filename),
            ]
        else:
            raise RuntimeError("No supported camera stack is available")

        result = subprocess.run(
            command,
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
    "has_rpicam_binaries",
    "capture_rpi_snapshot",
    "has_rpi_camera_stack",
    "record_rpi_video",
]
