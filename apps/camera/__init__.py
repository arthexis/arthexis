"""Backward compatibility shims for the legacy camera module."""

from apps.video import (
    CAMERA_DIR,
    RPI_CAMERA_BINARIES,
    RPI_CAMERA_DEVICE,
    capture_rpi_snapshot,
    has_rpi_camera_stack,
    record_rpi_video,
)

__all__ = [
    "CAMERA_DIR",
    "RPI_CAMERA_BINARIES",
    "RPI_CAMERA_DEVICE",
    "capture_rpi_snapshot",
    "has_rpi_camera_stack",
    "record_rpi_video",
]
