from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.urls import reverse

from apps.base.models import Entity
from .utils import (
    RPI_CAMERA_BINARIES,
    RPI_CAMERA_DEVICE,
    has_rpi_camera_stack,
    record_rpi_video,
)


@dataclass(frozen=True)
class DetectedVideoDevice:
    identifier: str
    description: str
    raw_info: str


class VideoDevice(Entity):
    """Detected video capture device available to a node."""

    node = models.ForeignKey(
        "nodes.Node", on_delete=models.CASCADE, related_name="video_devices"
    )
    identifier = models.CharField(max_length=100)
    description = models.CharField(max_length=255, blank=True)
    raw_info = models.TextField(blank=True)
    is_default = models.BooleanField(default=False)

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
        return f"{self.identifier} ({self.node})"

    @classmethod
    def detect_devices(cls) -> list[DetectedVideoDevice]:
        """Return detected video devices for the Raspberry Pi stack."""

        if not has_rpi_camera_stack():
            return []
        identifier = str(RPI_CAMERA_DEVICE)
        description = "Raspberry Pi Camera"
        raw_info = f"device={identifier} binaries={', '.join(RPI_CAMERA_BINARIES)}"
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
        created = 0
        updated = 0
        existing = {device.identifier: device for device in cls.objects.filter(node=node)}
        seen: set[str] = set()

        for device in detected:
            seen.add(device.identifier)
            obj = existing.get(device.identifier)
            defaults = {
                "description": device.description,
                "raw_info": device.raw_info,
                "is_default": True,
            }
            if obj is None:
                cls.objects.create(node=node, identifier=device.identifier, **defaults)
                created += 1
            else:
                dirty = False
                for field, value in defaults.items():
                    if getattr(obj, field) != value:
                        setattr(obj, field, value)
                        dirty = True
                if dirty:
                    obj.save(update_fields=list(defaults.keys()))
                    updated += 1

        cls.objects.filter(node=node).exclude(identifier__in=seen).delete()
        return created, updated

    @classmethod
    def has_video_device(cls) -> bool:
        """Return ``True`` when a Raspberry Pi video device is available."""

        return bool(cls.detect_devices())


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

    class Meta:
        verbose_name = _("MJPEG Stream")
        verbose_name_plural = _("MJPEG Streams")

    def get_stream_url(self) -> str:
        return reverse("video:mjpeg-stream", args=[self.slug])

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

    def mjpeg_stream(self):
        boundary = b"--frame\r\n"
        content_type = b"Content-Type: image/jpeg\r\n\r\n"

        for frame in self.iter_frame_bytes():
            yield boundary + content_type + frame + b"\r\n"


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
