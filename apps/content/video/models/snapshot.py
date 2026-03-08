"""Video snapshot persistence model."""

from __future__ import annotations

import base64
import logging
from pathlib import Path

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from PIL import Image

from apps.base.models import Entity
from apps.content.common.artifacts import resolve_sample_path
from apps.content.models import ContentSample

logger = logging.getLogger(__name__)

MAX_DATA_URI_SIZE_BYTES = 10 * 1024 * 1024


class VideoSnapshot(Entity):
    """Snapshot image metadata captured from a :class:`VideoDevice`."""

    device = models.ForeignKey(
        "video.VideoDevice", on_delete=models.CASCADE, related_name="snapshots"
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

    def __str__(self) -> str:  # pragma: no cover
        return _("Snapshot for %(device)s") % {"device": self.device}

    @staticmethod
    def _resolve_path(sample: ContentSample) -> Path:
        return resolve_sample_path(sample.path)

    @classmethod
    def build_metadata(cls, sample: ContentSample) -> dict[str, object]:
        """Build persisted snapshot metadata from an image sample."""

        width: int | None = None
        height: int | None = None
        image_format = ""
        file_path = cls._resolve_path(sample)
        try:
            with Image.open(file_path) as image:
                width, height = image.size
                image_format = image.format or ""
        except Exception as exc:  # pragma: no cover
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
        """Return a base64 data URI for the snapshot image when reasonably sized."""

        file_path = self._resolve_path(self.sample)
        if not file_path.exists():
            return None
        if file_path.stat().st_size > MAX_DATA_URI_SIZE_BYTES:
            logger.warning(
                "Skipping snapshot data URI for %s because size exceeds %s bytes",
                file_path,
                MAX_DATA_URI_SIZE_BYTES,
            )
            return None
        data = base64.b64encode(file_path.read_bytes()).decode("ascii")
        mime = (self.image_format or "jpeg").lower()
        return f"data:image/{mime};base64,{data}"
