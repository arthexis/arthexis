from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

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
