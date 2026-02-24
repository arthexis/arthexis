"""Operational helpers for video capture and frame rotation."""

from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image

logger = logging.getLogger(__name__)


def apply_image_rotation(path: Path, angle: int) -> None:
    """Rotate an image file in-place using a counterclockwise angle."""

    normalized = int(angle or 0) % 360
    if normalized == 0:
        return

    transpose_map = {
        90: Image.Transpose.ROTATE_90,
        180: Image.Transpose.ROTATE_180,
        270: Image.Transpose.ROTATE_270,
    }
    operation = transpose_map.get(normalized)
    if operation is None:
        return

    try:
        with Image.open(path) as image:
            fmt = image.format
            rotated = image.transpose(operation)
            if fmt:
                rotated.save(path, format=fmt)
            else:
                rotated.save(path)
    except Exception as exc:  # pragma: no cover - best-effort rotation
        logger.warning("Unable to auto-rotate snapshot %s: %s", path, exc)


def rotate_cv2_frame(frame, *, angle: int, cv2):
    """Rotate an OpenCV frame using the configured camera angle."""

    normalized = int(angle or 0) % 360
    if normalized == 90:
        return cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
    if normalized == 180:
        return cv2.rotate(frame, cv2.ROTATE_180)
    if normalized == 270:
        return cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
    return frame
