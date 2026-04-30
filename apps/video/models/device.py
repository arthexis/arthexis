"""Video device models and related discovery/synchronization logic."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import uuid

from django.db import models
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from apps.content.utils import save_screenshot
from apps.core.models.ownable import Ownable
from apps.nodes.device_sync import sync_detected_devices
from apps.nodes.feature_detection import is_feature_active_for_node
from apps.video.services.capture import apply_image_rotation
from apps.video.utils import (
    RPI_CAMERA_BINARIES,
    RPI_CAMERA_DEVICE,
    capture_cv2_snapshot,
    capture_rpi_snapshot,
    detect_cv2_camera_devices,
    probe_rpi_camera_stack,
)


@dataclass(frozen=True)
class DetectedVideoDevice:
    """Lightweight representation of a detected system video device."""

    identifier: str
    description: str
    raw_info: str


class VideoDevice(Ownable):
    """Detected video capture device available to a node."""

    owner_required = False
    DEFAULT_NAME = "BASE"
    AUTO_ROTATE_CHOICES = ((0, "0°"), (90, "90°"), (180, "180°"), (270, "270°"))

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
    auto_rotate = models.PositiveSmallIntegerField(
        default=0,
        choices=AUTO_ROTATE_CHOICES,
        help_text=_("Rotate captured frames counterclockwise."),
    )

    class Meta:
        ordering = ["identifier"]
        constraints = [
            models.UniqueConstraint(
                fields=["node", "identifier"], name="video_videodevice_unique"
            )
        ]
        verbose_name = _("Video Device")
        verbose_name_plural = _("Video Devices")

    def __str__(self) -> str:  # pragma: no cover
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
        """Return detected video devices for the local video stack."""

        probe = probe_rpi_camera_stack()
        if not probe.available:
            return [
                DetectedVideoDevice(
                    identifier=device.identifier,
                    description=device.description,
                    raw_info=device.raw_info,
                )
                for device in detect_cv2_camera_devices()
            ]
        identifier = str(RPI_CAMERA_DEVICE)
        if probe.backend == "rpicam":
            description = "Raspberry Pi Camera"
            raw_info = (
                f"device={identifier} binaries={', '.join(RPI_CAMERA_BINARIES)} "
                f"cameras={probe.detected_cameras}"
            )
        else:
            description = "Video4Linux Camera"
            raw_info = f"device={identifier} backend=ffmpeg reason={probe.reason}"
        return [
            DetectedVideoDevice(
                identifier=identifier,
                description=description,
                raw_info=raw_info,
            )
        ]

    @classmethod
    def refresh_from_system(
        cls, *, node, return_objects: bool = False
    ) -> tuple[int, int] | tuple[int, int, list["VideoDevice"], list["VideoDevice"]]:
        """Synchronize :class:`VideoDevice` entries for ``node``."""

        detected: list[DetectedVideoDevice] = []
        if is_feature_active_for_node(node=node, slug="video-cam"):
            detected = cls.detect_devices()
        result = sync_detected_devices(
            model_cls=cls,
            node=node,
            detected=detected,
            identifier_getter=lambda device: device.identifier,
            defaults_getter=lambda device: {
                "description": device.description,
                "raw_info": device.raw_info,
            },
            return_objects=return_objects,
        )
        cls._ensure_single_default_for_node(node)
        return result

    @classmethod
    def _ensure_single_default_for_node(cls, node) -> None:
        """Ensure a node has at most one default video device."""

        defaults = list(cls.objects.filter(node=node, is_default=True).order_by("pk"))
        if len(defaults) == 1:
            return
        if len(defaults) > 1:
            cls.objects.filter(pk__in=[device.pk for device in defaults[1:]]).update(
                is_default=False
            )
            return

        first_device = cls.objects.filter(node=node).order_by("identifier", "pk").first()
        if first_device is not None:
            first_device.is_default = True
            first_device.save(update_fields=["is_default"])

    @classmethod
    def get_default_for_node(cls, node) -> "VideoDevice | None":
        return (
            cls.objects.filter(node=node).order_by("-is_default", "pk").first()
            if node
            else None
        )

    @classmethod
    def has_video_device(cls) -> bool:
        """Return ``True`` when a local video device is available."""

        return bool(cls.detect_devices())

    def get_latest_snapshot(self):
        return (
            self.snapshots.select_related("sample")
            .order_by("-captured_at", "-pk")
            .first()
        )

    def capture_snapshot_path(self) -> Path:
        """Capture a snapshot file path and apply auto-rotation when configured."""

        if self.identifier == str(RPI_CAMERA_DEVICE):
            path = capture_rpi_snapshot(
                width=self.capture_width,
                height=self.capture_height,
            )
            apply_image_rotation(path, int(self.auto_rotate or 0))
        else:
            path = capture_cv2_snapshot(
                self.identifier,
                width=self.capture_width,
                height=self.capture_height,
                auto_rotate=int(self.auto_rotate or 0),
            )
        return path

    def capture_snapshot(self, *, link_duplicates: bool = False):
        """Capture and persist a :class:`VideoSnapshot` for this device."""

        from apps.video.models.snapshot import VideoSnapshot

        path = self.capture_snapshot_path()
        sample = save_screenshot(
            path,
            node=self.node,
            method=(
                "RPI_CAMERA"
                if self.identifier == str(RPI_CAMERA_DEVICE)
                else "OPENCV_CAMERA"
            ),
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
            update_fields: list[str] = []
            for field, value in metadata.items():
                if getattr(snapshot, field) != value:
                    setattr(snapshot, field, value)
                    update_fields.append(field)
            if update_fields:
                snapshot.save(update_fields=update_fields)
        return snapshot
