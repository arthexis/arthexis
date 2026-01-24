import logging

from celery import shared_task
from django.utils import timezone

from .models import MjpegStream

logger = logging.getLogger(__name__)


@shared_task
def capture_mjpeg_thumbnails() -> dict[str, int]:
    """Capture thumbnails for active MJPEG streams that are due."""

    now = timezone.localtime()
    captured = 0
    skipped = 0
    for stream in MjpegStream.objects.filter(is_active=True):
        frequency = stream.thumbnail_frequency or 0
        if frequency <= 0:
            skipped += 1
            continue
        last = stream.last_thumbnail_at
        if last and (now - last).total_seconds() < frequency:
            skipped += 1
            continue
        try:
            frame_bytes = stream.capture_frame_bytes()
        except Exception as exc:  # pragma: no cover - depends on device stack
            logger.warning("Unable to capture MJPEG thumbnail for %s: %s", stream, exc)
            continue
        if not frame_bytes:
            logger.warning("No frame captured for MJPEG stream %s", stream)
            continue
        stream.store_frame_bytes(frame_bytes, update_thumbnail=True)
        captured += 1
    return {"captured": captured, "skipped": skipped}
