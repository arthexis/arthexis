"""Helpers for bridging camera frames into the media/classification pipeline."""

from __future__ import annotations

from django.utils import timezone

from apps.media.utils import ensure_media_bucket
from apps.video.frame_cache import get_frame

from .ingest import create_media_file_from_bytes


CAMERA_CLASSIFICATION_BUCKET_SLUG = "camera-classification"


def ensure_camera_classification_bucket():
    """Return the media bucket used for camera-derived classification frames."""

    return ensure_media_bucket(
        slug=CAMERA_CLASSIFICATION_BUCKET_SLUG,
        name="Camera Classification Frames",
        allowed_patterns="*.jpg\n*.jpeg\n*.png",
    )


def capture_stream_frame_bytes(stream) -> tuple[bytes | None, str | None]:
    """Return a single frame for ``stream`` from Redis or direct capture."""

    cached = get_frame(stream)
    if cached and cached.frame_bytes:
        return cached.frame_bytes, "redis-cache"
    frame_bytes = stream.capture_frame_bytes()
    if frame_bytes:
        return frame_bytes, "direct-capture"
    return None, None


def create_media_file_from_frame_bytes(
    frame_bytes: bytes,
    *,
    original_name: str,
    content_type: str = "image/jpeg",
):
    """Persist frame bytes as a `MediaFile` for the classifier pipeline."""

    return create_media_file_from_bytes(
        frame_bytes,
        bucket_slug=CAMERA_CLASSIFICATION_BUCKET_SLUG,
        bucket_name="Camera Classification Frames",
        original_name=original_name,
        content_type=content_type,
        queue_for_classification=False,
    )


def capture_stream_to_media_file(stream):
    """Capture one frame from ``stream`` and persist it as a media file."""

    frame_bytes, source = capture_stream_frame_bytes(stream)
    if not frame_bytes:
        return None, None
    timestamp = timezone.localtime().strftime("%Y%m%d%H%M%S%f")
    original_name = f"{stream.slug}-{timestamp}.jpg"
    return create_media_file_from_frame_bytes(frame_bytes, original_name=original_name), source
