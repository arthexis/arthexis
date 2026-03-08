"""Video recording persistence model."""

from __future__ import annotations

from pathlib import Path

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.base.models import Entity
from apps.content.video.utils import record_rpi_video


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

    def __str__(self) -> str:  # pragma: no cover
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

        recorded_at = timezone.now()
        video_path = record_rpi_video(duration_seconds=duration_seconds, timeout=timeout)
        return cls.objects.create(
            node=node,
            path=str(video_path),
            recorded_at=recorded_at,
            duration_seconds=max(duration_seconds, 0),
            method=cls.METHOD_RPI_CAMERA,
        )
