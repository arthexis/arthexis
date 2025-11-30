from __future__ import annotations

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


def has_rpi_camera_stack() -> bool:
    """Return ``True`` when the Raspberry Pi camera stack is available."""

    device = RPI_CAMERA_DEVICE
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


__all__ = [
    "CAMERA_DIR",
    "RPI_CAMERA_BINARIES",
    "RPI_CAMERA_DEVICE",
    "capture_rpi_snapshot",
    "has_rpi_camera_stack",
]
