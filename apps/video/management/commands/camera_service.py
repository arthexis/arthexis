from __future__ import annotations

import logging
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.video.frame_cache import frame_cache_url, store_frame, store_status
from apps.video.models import MjpegDependencyError, MjpegStream

logger = logging.getLogger("apps.video.camera_service")


class _StreamCapture:
    def __init__(self, stream: MjpegStream):
        self.stream = stream
        self._cv2 = None
        self._capture = None
        self._last_capture = 0.0
        self._last_error: str | None = None
        self._last_logged_error: str | None = None

    def _ensure_capture(self) -> bool:
        if self._cv2 is None:
            self._cv2 = self.stream._load_cv2()
        if self._capture is None:
            self._capture = self._cv2.VideoCapture(self.stream.video_device.identifier)
        if not self._capture.isOpened():
            self._capture.release()
            self._capture = None
            self._last_error = "Unable to open video device"
            return False
        return True

    def close(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None

    def capture_frame(self, *, interval: float) -> bytes | None:
        now = time.monotonic()
        if (now - self._last_capture) < interval:
            return None
        if not self._ensure_capture():
            return None
        self._last_capture = now
        success, frame = self._capture.read()
        if not success:
            self._last_error = "Camera read failed"
            return None
        frame = self.stream._rotate_frame(frame, self._cv2)
        success, buffer = self._cv2.imencode(".jpg", frame)
        if not success:
            self._last_error = "JPEG encode failed"
            return None
        self._last_error = None
        return buffer.tobytes()

    def status_payload(self) -> dict[str, object]:
        return {
            "stream": self.stream.slug,
            "device": self.stream.video_device.identifier,
            "last_error": self._last_error,
            "updated_at": timezone.now().isoformat(),
        }

    def log_status(self) -> None:
        if self._last_error == self._last_logged_error:
            return
        self._last_logged_error = self._last_error
        if self._last_error:
            logger.warning(
                "Camera service error for %s (%s): %s",
                self.stream.slug,
                self.stream.video_device.identifier,
                self._last_error,
            )
        else:
            logger.info(
                "Camera service recovered for %s (%s)",
                self.stream.slug,
                self.stream.video_device.identifier,
            )


class Command(BaseCommand):
    help = "Run the MJPEG camera capture service that writes frames to Redis."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--interval",
            type=float,
            default=float(getattr(settings, "VIDEO_FRAME_CAPTURE_INTERVAL", 0.2) or 0.2),
            help="Seconds between frame capture attempts per stream.",
        )
        parser.add_argument(
            "--sleep",
            type=float,
            default=float(getattr(settings, "VIDEO_FRAME_SERVICE_SLEEP", 0.05) or 0.05),
            help="Seconds to sleep between capture loops.",
        )

    def handle(self, *args, **options) -> None:
        interval = float(options["interval"])
        sleep = float(options["sleep"])
        if not frame_cache_url():
            raise CommandError("A Redis URL must be configured to use camera_service.")

        captures: dict[int, _StreamCapture] = {}
        self.stdout.write(self.style.SUCCESS("Starting camera service..."))
        try:
            while True:
                streams = (
                    MjpegStream.objects.filter(is_active=True)
                    .select_related("video_device")
                    .order_by("pk")
                )
                active_ids = {stream.pk for stream in streams}
                for stream_id in list(captures):
                    if stream_id not in active_ids:
                        captures[stream_id].close()
                        captures.pop(stream_id, None)
                for stream in streams:
                    capture = captures.get(stream.pk)
                    if capture is None:
                        capture = _StreamCapture(stream)
                        captures[stream.pk] = capture
                    try:
                        frame_bytes = capture.capture_frame(interval=interval)
                    except MjpegDependencyError as exc:
                        capture._last_error = str(exc)
                        logger.warning("MJPEG dependency error for %s: %s", stream.slug, exc)
                        store_status(stream, capture.status_payload())
                        capture.log_status()
                        continue
                    except Exception as exc:  # pragma: no cover - runtime device error
                        capture._last_error = str(exc)
                        logger.warning("Camera capture error for %s: %s", stream.slug, exc)
                        store_status(stream, capture.status_payload())
                        capture.log_status()
                        continue
                    payload = capture.status_payload()
                    if frame_bytes:
                        store_frame(stream, frame_bytes)
                    if frame_bytes or payload.get("last_error"):
                        store_status(stream, payload)
                        capture.log_status()
                time.sleep(sleep)
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING("Camera service stopped."))
        finally:
            for capture in captures.values():
                capture.close()
