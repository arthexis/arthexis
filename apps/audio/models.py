from __future__ import annotations

from dataclasses import dataclass
import re
from pathlib import Path
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.base.models import Entity


PCM_PATH = Path("/proc/asound/pcm")


@dataclass(frozen=True)
class DetectedRecordingDevice:
    identifier: str
    description: str
    capture_channels: int
    raw_info: str


class RecordingDevice(Entity):
    """Detected recording device available to a node."""

    node = models.ForeignKey(
        "nodes.Node", on_delete=models.CASCADE, related_name="recording_devices"
    )
    identifier = models.CharField(max_length=50)
    description = models.CharField(max_length=255, blank=True)
    capture_channels = models.PositiveIntegerField(default=0)
    raw_info = models.TextField(blank=True)

    class Meta:
        ordering = ["identifier"]
        constraints = [
            models.UniqueConstraint(
                fields=["node", "identifier"], name="audio_recordingdevice_unique"
            )
        ]
        verbose_name = _("Recording Device")
        verbose_name_plural = _("Recording Devices")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.identifier} ({self.node})"

    @classmethod
    def parse_devices(cls, *, pcm_path: Path = PCM_PATH) -> list[DetectedRecordingDevice]:
        """Return detected recording devices from ``pcm_path``."""

        try:
            contents = pcm_path.read_text(errors="ignore")
        except OSError:
            return []

        devices: list[DetectedRecordingDevice] = []
        for line in contents.splitlines():
            raw_line = line.strip()
            if not raw_line:
                continue
            capture_match = re.search(r"capture\s+(\d+)", raw_line, re.IGNORECASE)
            if not capture_match:
                continue
            capture_channels = int(capture_match.group(1))
            if capture_channels <= 0:
                continue
            descriptor_match = re.match(r"(?P<identifier>[^:]+):\s*(?P<description>[^:]*)", raw_line)
            identifier = descriptor_match.group("identifier").strip() if descriptor_match else raw_line
            description = (descriptor_match.group("description") or "").strip() if descriptor_match else ""
            devices.append(
                DetectedRecordingDevice(
                    identifier=identifier,
                    description=description,
                    capture_channels=capture_channels,
                    raw_info=raw_line,
                )
            )
        return devices

    @classmethod
    def refresh_from_system(
        cls, *, node, pcm_path: Path = PCM_PATH
    ) -> tuple[int, int]:
        """Synchronize :class:`RecordingDevice` entries for ``node``.

        Returns a ``(created, updated)`` tuple.
        """

        detected = cls.parse_devices(pcm_path=pcm_path)
        created = 0
        updated = 0
        existing = {
            device.identifier: device for device in cls.objects.filter(node=node)
        }
        seen: set[str] = set()
        for device in detected:
            seen.add(device.identifier)
            obj = existing.get(device.identifier)
            defaults = {
                "description": device.description,
                "capture_channels": device.capture_channels,
                "raw_info": device.raw_info,
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
    def has_recording_device(cls, *, pcm_path: Path = PCM_PATH) -> bool:
        """Return ``True`` when a recording device is available."""

        return bool(cls.parse_devices(pcm_path=pcm_path))
