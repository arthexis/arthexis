"""Models for stream configuration and MJPEG metadata."""

from __future__ import annotations

import base64
import hashlib
import io
import logging
from pathlib import Path
from typing import Iterator

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from PIL import Image

from apps.base.models import Entity
from apps.content.models import ContentSample
from apps.content.utils import save_content_sample
from apps.content.video.services.mjpeg import (
    MjpegDependencyError,
    MjpegDeviceUnavailableError,
    capture_device_frame_bytes,
    encode_mjpeg_chunk,
    iter_device_frame_bytes,
)

logger = logging.getLogger(__name__)


class VideoStream(Entity):
    """Base configuration for a video stream that can be exposed publicly."""

    name = models.CharField(max_length=255)
    slug = models.SlugField(unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        abstract = True
        ordering = ["name"]

    def __str__(self) -> str:  # pragma: no cover
        return self.name or self.slug

    def get_absolute_url(self) -> str:
        return reverse("video:stream-detail", args=[self.slug])

    def get_admin_url(self) -> str:
        return reverse(
            f"admin:{self._meta.app_label}_{self._meta.model_name}_change",
            args=[self.pk],
        )


class MjpegStream(VideoStream):
    """Multipart JPEG stream sourced from a configured video device."""

    video_device = models.ForeignKey(
        "video.VideoDevice",
        on_delete=models.PROTECT,
        related_name="mjpeg_streams",
    )
    last_frame_sample = models.ForeignKey(
        ContentSample,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mjpeg_frames",
    )
    last_frame_captured_at = models.DateTimeField(null=True, blank=True)
    last_thumbnail_sample = models.ForeignKey(
        ContentSample,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mjpeg_thumbnails",
    )
    last_thumbnail_at = models.DateTimeField(null=True, blank=True)
    thumbnail_frequency = models.PositiveIntegerField(
        default=60,
        help_text=_(
            "Seconds between automatic thumbnail captures even without active viewers."
        ),
    )

    class Meta:
        verbose_name = _("MJPEG Stream")
        verbose_name_plural = _("MJPEG Streams")

    def get_stream_url(self) -> str:
        return reverse("video:mjpeg-stream", args=[self.slug])

    def get_stream_ws_path(self) -> str:
        return f"/ws/video/{self.slug}/"

    def get_webrtc_ws_path(self) -> str:
        return f"/ws/video/{self.slug}/webrtc/"

    @staticmethod
    def _resolve_sample_path(sample: ContentSample) -> Path:
        file_path = Path(sample.path)
        if not file_path.is_absolute():
            file_path = settings.LOG_DIR / file_path
        return file_path

    def _data_uri_for_sample(self, sample: ContentSample | None) -> str | None:
        if not sample:
            return None
        file_path = self._resolve_sample_path(sample)
        if not file_path.exists():
            return None
        raw = file_path.read_bytes()
        try:
            with Image.open(io.BytesIO(raw)) as image:
                mime = (image.format or "jpeg").lower()
        except Exception:  # pragma: no cover
            mime = "jpeg"
        data = base64.b64encode(raw).decode("ascii")
        return f"data:image/{mime};base64,{data}"

    def get_last_frame_data_uri(self) -> str | None:
        return self._data_uri_for_sample(self.last_frame_sample)

    def get_thumbnail_data_uri(self) -> str | None:
        return self._data_uri_for_sample(self.last_thumbnail_sample)

    def _write_frame_file(self, frame_bytes: bytes, *, suffix: str) -> Path:
        storage_dir = settings.LOG_DIR / "video" / "streams"
        storage_dir.mkdir(parents=True, exist_ok=True)
        timestamp = timezone.localtime().strftime("%Y%m%d%H%M%S%f")
        slug = self.slug or f"stream-{self.pk or 'unknown'}"
        file_path = storage_dir / f"{slug}-{timestamp}-{suffix}.jpg"
        file_path.write_bytes(frame_bytes)
        return file_path

    def _save_frame_sample(
        self,
        frame_bytes: bytes,
        *,
        suffix: str,
        method: str,
    ) -> ContentSample | None:
        digest = hashlib.sha256(frame_bytes).hexdigest()
        existing = ContentSample.objects.filter(hash=digest).first()
        if existing:
            return existing
        file_path = self._write_frame_file(frame_bytes, suffix=suffix)
        sample = save_content_sample(
            path=file_path,
            kind=ContentSample.IMAGE,
            method=method,
            link_duplicates=True,
            duplicate_log_context="MJPEG frame",
        )
        if sample and sample.path:
            sample_path = self._resolve_sample_path(sample)
            if sample_path.resolve() != file_path.resolve():
                try:
                    file_path.unlink()
                except OSError:  # pragma: no cover
                    logger.warning(
                        "Unable to remove duplicate MJPEG frame file %s", file_path
                    )
        return sample

    def _build_thumbnail_bytes(self, frame_bytes: bytes) -> bytes:
        with Image.open(io.BytesIO(frame_bytes)) as image:
            if image.mode not in ("RGB", "L"):
                image = image.convert("RGB")
            image.thumbnail((320, 320), Image.LANCZOS)
            output = io.BytesIO()
            image.save(output, format="JPEG")
        return output.getvalue()

    def store_frame_bytes(self, frame_bytes: bytes, *, update_thumbnail: bool = True) -> None:
        if not frame_bytes:
            return
        now = timezone.localtime()
        frame_sample = self._save_frame_sample(
            frame_bytes, suffix="frame", method="MJPEG_STREAM"
        )
        updates: dict[str, object] = {"last_frame_captured_at": now}
        if frame_sample and frame_sample != self.last_frame_sample:
            updates["last_frame_sample"] = frame_sample
        if update_thumbnail:
            try:
                thumb_bytes = self._build_thumbnail_bytes(frame_bytes)
            except Exception as exc:  # pragma: no cover
                logger.warning("Unable to build MJPEG thumbnail: %s", exc)
            else:
                thumb_sample = self._save_frame_sample(
                    thumb_bytes, suffix="thumb", method="MJPEG_THUMBNAIL"
                )
                updates["last_thumbnail_at"] = now
                if thumb_sample and thumb_sample != self.last_thumbnail_sample:
                    updates["last_thumbnail_sample"] = thumb_sample
        if updates:
            for field, value in updates.items():
                setattr(self, field, value)
            self.save(update_fields=list(updates.keys()))

    def _load_cv2(self):
        """Compatibility wrapper for camera_service; delegates to service layer."""

        from apps.content.video.services.mjpeg import load_cv2

        return load_cv2()

    def _rotate_frame(self, frame, cv2):
        """Compatibility wrapper for camera_service frame auto-rotation."""

        from apps.content.video.services.capture import rotate_cv2_frame

        return rotate_cv2_frame(frame, angle=int(self.video_device.auto_rotate or 0), cv2=cv2)

    def iter_frame_bytes(self):
        """Yield encoded JPEG frames from the configured capture device."""

        yield from iter_device_frame_bytes(
            device_identifier=self.video_device.identifier,
            auto_rotate=int(self.video_device.auto_rotate or 0),
        )

    def capture_frame_bytes(self) -> bytes | None:
        """Capture a single encoded JPEG frame from this stream's device."""

        return capture_device_frame_bytes(
            device_identifier=self.video_device.identifier,
            auto_rotate=int(self.video_device.auto_rotate or 0),
        )

    def mjpeg_stream(
        self,
        frame_iter: Iterator[bytes] | None = None,
        *,
        first_frame: bytes | None = None,
    ):
        """Yield multipart MJPEG bytes and persist the final frame."""

        active_iter = frame_iter if frame_iter is not None else self.iter_frame_bytes()
        last_frame: bytes | None = None
        try:
            if first_frame is not None:
                last_frame = first_frame
                yield encode_mjpeg_chunk(first_frame)
            for frame in active_iter:
                last_frame = frame
                yield encode_mjpeg_chunk(frame)
        finally:
            if last_frame:
                try:
                    self.store_frame_bytes(last_frame, update_thumbnail=True)
                except Exception as exc:  # pragma: no cover
                    logger.warning("Unable to persist final MJPEG frame: %s", exc)
            if active_iter is not None and hasattr(active_iter, "close"):
                try:
                    active_iter.close()
                except Exception as exc:  # pragma: no cover
                    logger.warning("Unable to close MJPEG frame iterator: %s", exc)
