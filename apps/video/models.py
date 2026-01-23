from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import io
import logging
from pathlib import Path
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _
from django.urls import reverse
from PIL import Image

from apps.content.models import ContentSample
from apps.content.utils import save_content_sample

from apps.base.models import Entity
from apps.core.models.ownable import Ownable
from apps.nodes.device_sync import sync_detected_devices
from apps.content.utils import save_screenshot
from .utils import (
    RPI_CAMERA_BINARIES,
    RPI_CAMERA_DEVICE,
    capture_rpi_snapshot,
    has_rpi_camera_stack,
    has_rpicam_binaries,
    record_rpi_video,
)


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DetectedVideoDevice:
    identifier: str
    description: str
    raw_info: str


class VideoDevice(Ownable):
    """Detected video capture device available to a node."""

    owner_required = False
    DEFAULT_NAME = "BASE (migrate)"

    node = models.ForeignKey(
        "nodes.Node", on_delete=models.CASCADE, related_name="video_devices"
    )
    name = models.CharField(max_length=255, default=DEFAULT_NAME)
    slug = models.SlugField(max_length=255, blank=True, default="")
    identifier = models.CharField(max_length=100)
    description = models.CharField(max_length=255, blank=True)
    raw_info = models.TextField(blank=True)
    is_default = models.BooleanField(default=False)
    capture_width = models.PositiveIntegerField(null=True, blank=True)
    capture_height = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["identifier"]
        constraints = [
            models.UniqueConstraint(
                fields=["node", "identifier"], name="video_videodevice_unique"
            )
        ]
        verbose_name = _("Video Device")
        verbose_name_plural = _("Video Devices")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.display_name} ({self.node})"

    @property
    def display_name(self) -> str:
        name = (self.name or "").strip()
        if name:
            return name
        slug = (self.slug or "").strip()
        if slug:
            return slug
        return self.identifier

    def save(self, *args, **kwargs) -> None:
        if not (self.name or "").strip():
            self.name = self.DEFAULT_NAME
        self.slug = (self.slug or "").strip()
        if not self.slug:
            self.slug = slugify(self.name)
        if not self.slug:
            self.slug = uuid.uuid4().hex[:12]
        super().save(*args, **kwargs)

    @property
    def is_public(self) -> bool:
        return not self.user_id and not self.group_id

    def owner_display(self) -> str:
        if self.is_public:
            return _("Public")
        return super().owner_display()

    @classmethod
    def detect_devices(cls) -> list[DetectedVideoDevice]:
        """Return detected video devices for the Raspberry Pi stack."""

        if not has_rpi_camera_stack():
            return []
        identifier = str(RPI_CAMERA_DEVICE)
        if has_rpicam_binaries():
            description = "Raspberry Pi Camera"
            raw_info = f"device={identifier} binaries={', '.join(RPI_CAMERA_BINARIES)}"
        else:
            description = "Video4Linux Camera"
            raw_info = f"device={identifier} binaries=ffmpeg"
        return [
            DetectedVideoDevice(
                identifier=identifier,
                description=description,
                raw_info=raw_info,
            )
        ]

    @classmethod
    def refresh_from_system(cls, *, node) -> tuple[int, int]:
        """Synchronize :class:`VideoDevice` entries for ``node``.

        Returns a ``(created, updated)`` tuple.
        """

        detected = cls.detect_devices()
        return sync_detected_devices(
            model_cls=cls,
            node=node,
            detected=detected,
            identifier_getter=lambda device: device.identifier,
            defaults_getter=lambda device: {
                "description": device.description,
                "raw_info": device.raw_info,
                "is_default": True,
            },
        )

    @classmethod
    def has_video_device(cls) -> bool:
        """Return ``True`` when a Raspberry Pi video device is available."""

        return bool(cls.detect_devices())

    def get_latest_snapshot(self):
        return self.snapshots.select_related("sample").order_by("-captured_at", "-pk").first()

    def capture_snapshot(self, *, link_duplicates: bool = False):
        path = capture_rpi_snapshot(width=self.capture_width, height=self.capture_height)
        sample = save_screenshot(
            path,
            node=self.node,
            method="RPI_CAMERA",
            link_duplicates=link_duplicates,
        )
        if not sample:
            return None

        metadata = VideoSnapshot.build_metadata(sample)
        snapshot, created = VideoSnapshot.objects.get_or_create(
            device=self,
            sample=sample,
            defaults=metadata,
        )
        if not created:
            updates: dict[str, object] = {}
            for field, value in metadata.items():
                if getattr(snapshot, field) != value:
                    setattr(snapshot, field, value)
                    updates[field] = value
            if updates:
                snapshot.save(update_fields=list(updates.keys()))
        return snapshot


class VideoStream(Entity):
    """Base configuration for a video stream that can be exposed publicly."""

    name = models.CharField(max_length=255)
    slug = models.SlugField(unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        abstract = True
        ordering = ["name"]

    def __str__(self) -> str:  # pragma: no cover - simple representation
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
        VideoDevice,
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
        try:
            with Image.open(file_path) as image:
                mime = (image.format or "jpeg").lower()
        except Exception:  # pragma: no cover - best-effort metadata
            mime = "jpeg"
        data = base64.b64encode(file_path.read_bytes()).decode("ascii")
        return f"data:image/{mime};base64,{data}"

    def get_last_frame_data_uri(self) -> str | None:
        return self._data_uri_for_sample(self.last_frame_sample)

    def get_thumbnail_data_uri(self) -> str | None:
        return self._data_uri_for_sample(self.last_thumbnail_sample)

    def _write_frame_file(self, frame_bytes: bytes, *, suffix: str) -> Path:
        storage_dir = settings.LOG_DIR / "video" / "streams"
        storage_dir.mkdir(parents=True, exist_ok=True)
        timestamp = timezone.now().strftime("%Y%m%d%H%M%S%f")
        slug = self.slug or f"stream-{self.pk or 'unknown'}"
        filename = f"{slug}-{timestamp}-{suffix}.jpg"
        file_path = storage_dir / filename
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
                except OSError:  # pragma: no cover - best-effort cleanup
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
        now = timezone.now()
        frame_sample = self._save_frame_sample(
            frame_bytes, suffix="frame", method="MJPEG_STREAM"
        )
        updates: dict[str, object] = {"last_frame_captured_at": now}
        if frame_sample and frame_sample != self.last_frame_sample:
            updates["last_frame_sample"] = frame_sample
        if update_thumbnail:
            try:
                thumb_bytes = self._build_thumbnail_bytes(frame_bytes)
            except Exception as exc:  # pragma: no cover - best-effort thumbnail
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

    def iter_frame_bytes(self):
        """Yield encoded JPEG frames from the configured capture device."""

        try:
            import cv2  # type: ignore
        except ImportError as exc:  # pragma: no cover - runtime dependency
            raise RuntimeError("MJPEG streaming requires the OpenCV (cv2) package") from exc

        capture = cv2.VideoCapture(self.video_device.identifier)

        if not capture.isOpened():
            capture.release()
            raise RuntimeError(
                _("Unable to open video device %(device)s")
                % {"device": self.video_device.identifier}
            )

        try:
            while True:
                success, frame = capture.read()
                if not success:
                    break
                success, buffer = cv2.imencode(".jpg", frame)
                if not success:
                    continue
                yield buffer.tobytes()
        finally:  # pragma: no cover - release resource
            capture.release()

    def capture_frame_bytes(self) -> bytes | None:
        try:
            import cv2  # type: ignore
        except ImportError as exc:  # pragma: no cover - runtime dependency
            raise RuntimeError("MJPEG streaming requires the OpenCV (cv2) package") from exc

        capture = cv2.VideoCapture(self.video_device.identifier)
        if not capture.isOpened():
            capture.release()
            raise RuntimeError(
                _("Unable to open video device %(device)s")
                % {"device": self.video_device.identifier}
            )
        try:
            success, frame = capture.read()
            if not success:
                return None
            success, buffer = cv2.imencode(".jpg", frame)
            if not success:
                return None
            return buffer.tobytes()
        finally:  # pragma: no cover - release resource
            capture.release()

    def mjpeg_stream(self):
        boundary = b"--frame\r\n"
        content_type = b"Content-Type: image/jpeg\r\n\r\n"
        last_frame: bytes | None = None
        try:
            for frame in self.iter_frame_bytes():
                last_frame = frame
                yield boundary + content_type + frame + b"\r\n"
        finally:
            if last_frame:
                try:
                    self.store_frame_bytes(last_frame, update_thumbnail=True)
                except Exception as exc:  # pragma: no cover - best-effort storage
                    logger.warning("Unable to persist final MJPEG frame: %s", exc)


class VideoSnapshot(Entity):
    device = models.ForeignKey(
        VideoDevice, on_delete=models.CASCADE, related_name="snapshots"
    )
    sample = models.ForeignKey(
        ContentSample, on_delete=models.CASCADE, related_name="video_snapshots"
    )
    captured_at = models.DateTimeField(default=timezone.now)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    image_format = models.CharField(max_length=50, blank=True)

    class Meta:
        ordering = ["-captured_at", "-id"]
        verbose_name = _("Video Snapshot")
        verbose_name_plural = _("Video Snapshots")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return _("Snapshot for %(device)s") % {"device": self.device}

    @staticmethod
    def _resolve_path(sample: ContentSample) -> Path:
        file_path = Path(sample.path)
        if not file_path.is_absolute():
            file_path = settings.LOG_DIR / file_path
        return file_path

    @classmethod
    def build_metadata(cls, sample: ContentSample) -> dict[str, object]:
        width: int | None = None
        height: int | None = None
        image_format = ""
        file_path = cls._resolve_path(sample)
        try:
            with Image.open(file_path) as image:
                width, height = image.size
                image_format = image.format or ""
        except Exception as exc:  # pragma: no cover - best-effort metadata
            logger.warning("Could not read image metadata from %s: %s", file_path, exc)
        return {
            "captured_at": sample.created_at,
            "width": width,
            "height": height,
            "image_format": image_format,
        }

    @property
    def resolution_display(self) -> str:
        if self.width and self.height:
            return f"{self.width} × {self.height}"
        return ""

    def get_data_uri(self) -> str | None:
        file_path = self._resolve_path(self.sample)
        if not file_path.exists():
            return None
        data = base64.b64encode(file_path.read_bytes()).decode("ascii")
        mime = (self.image_format or "jpeg").lower()
        return f"data:image/{mime};base64,{data}"


class YoutubeChannel(Entity):
    """YouTube channel reference tracked within Arthexis."""

    title = models.CharField(max_length=255)
    channel_id = models.CharField(
        max_length=64,
        unique=True,
        help_text=_("YouTube channel identifier (for example UC1234abcd)."),
    )
    handle = models.CharField(
        max_length=64,
        blank=True,
        help_text=_("Optional YouTube handle (for example @arthexis)."),
    )
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["title"]
        verbose_name = _("YouTube Channel")
        verbose_name_plural = _("YouTube Channels")
        constraints = [
            models.UniqueConstraint(
                fields=["handle"],
                condition=~models.Q(handle=""),
                name="youtubechannel_handle_unique",
            )
        ]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        title = (self.title or "").strip()
        if title:
            return title
        handle = self.get_handle(include_at=True)
        if handle:
            return handle
        if self.channel_id:
            return self.channel_id
        return super().__str__()

    def save(self, *args, **kwargs):
        self.channel_id = (self.channel_id or "").strip()
        self.handle = (self.handle or "").strip()
        super().save(*args, **kwargs)

    def get_handle(self, *, include_at: bool = False) -> str:
        """Return the normalized handle, optionally prefixed with ``@``."""

        handle = (self.handle or "").strip().lstrip("@")
        if include_at and handle:
            return f"@{handle}"
        return handle

    def get_channel_url(self) -> str:
        """Return the best YouTube URL for the channel."""

        handle = self.get_handle()
        if handle:
            return f"https://www.youtube.com/@{handle}"
        if self.channel_id:
            return f"https://www.youtube.com/channel/{self.channel_id}"
        return ""


class VideoRecording(Entity):
    """Stored reference to a video recording captured by a node."""

    METHOD_RPI_CAMERA = "RPI_CAMERA"
    METHOD_CHOICES = ((METHOD_RPI_CAMERA, _("Raspberry Pi Camera")),)

    node = models.ForeignKey(
        "nodes.Node", on_delete=models.CASCADE, related_name="video_recordings"
    )
    path = models.CharField(max_length=255)
    recorded_at = models.DateTimeField(default=timezone.now)
    duration_seconds = models.PositiveIntegerField(default=0)
    method = models.CharField(
        max_length=50, choices=METHOD_CHOICES, default=METHOD_RPI_CAMERA
    )

    class Meta:
        ordering = ["-recorded_at"]
        verbose_name = _("Video Recording")
        verbose_name_plural = _("Video Recordings")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{Path(self.path).name} ({self.node})"

    @classmethod
    def record_rpi_camera(
        cls, *, node=None, duration_seconds: int = 5, timeout: int = 15
    ) -> "VideoRecording":
        """Record a video using the Raspberry Pi camera stack and store it."""

        from apps.nodes.models import Node

        node = node or Node.get_local()
        if node is None:
            raise RuntimeError("No local node is registered to store the recording.")

        video_path = record_rpi_video(duration_seconds=duration_seconds, timeout=timeout)
        return cls.objects.create(
            node=node,
            path=str(video_path),
            duration_seconds=max(duration_seconds, 0),
            method=cls.METHOD_RPI_CAMERA,
        )
