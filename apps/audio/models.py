from __future__ import annotations

from dataclasses import dataclass
import base64
import logging
import mimetypes
import re
from pathlib import Path
import wave

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.base.models import Entity
from apps.content.models import ContentSample
from apps.nodes.device_sync import sync_detected_devices

logger = logging.getLogger(__name__)


PCM_PATH = Path("/proc/asound/pcm")


@dataclass(frozen=True)
class DetectedRecordingDevice:
    identifier: str
    description: str
    capture_channels: int
    raw_info: str


class RecordingDevice(Entity):
    """Detected recording device available to a node."""

    _ALSA_IDENTIFIER_RE = re.compile(r"(?P<card>\d+)-(?P<device>\d+)$")

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

    @staticmethod
    def _is_usb_device(device: DetectedRecordingDevice) -> bool:
        descriptor = f"{device.description} {device.raw_info}".lower()
        return "usb" in descriptor

    @staticmethod
    def identifier_to_alsa_device(identifier: str) -> str | None:
        match = RecordingDevice._ALSA_IDENTIFIER_RE.match(identifier)
        if not match:
            return None
        card = int(match.group("card"))
        device = int(match.group("device"))
        return f"plughw:{card},{device}"

    @classmethod
    def preferred_device(
        cls, *, pcm_path: Path = PCM_PATH
    ) -> DetectedRecordingDevice | None:
        devices = cls.parse_devices(pcm_path=pcm_path)
        if not devices:
            return None
        usb_devices = [device for device in devices if cls._is_usb_device(device)]
        return usb_devices[0] if usb_devices else devices[0]

    @classmethod
    def refresh_from_system(
        cls, *, node, pcm_path: Path = PCM_PATH
    ) -> tuple[int, int]:
        """Synchronize :class:`RecordingDevice` entries for ``node``.

        Returns a ``(created, updated)`` tuple.
        """

        detected = cls.parse_devices(pcm_path=pcm_path)
        return sync_detected_devices(
            model_cls=cls,
            node=node,
            detected=detected,
            identifier_getter=lambda device: device.identifier,
            defaults_getter=lambda device: {
                "description": device.description,
                "capture_channels": device.capture_channels,
                "raw_info": device.raw_info,
            },
        )

    @classmethod
    def has_recording_device(cls, *, pcm_path: Path = PCM_PATH) -> bool:
        """Return ``True`` when a recording device is available."""

        return bool(cls.parse_devices(pcm_path=pcm_path))


class AudioSample(Entity):
    """Metadata and playback helper for stored audio samples."""

    sample = models.ForeignKey(
        ContentSample, on_delete=models.CASCADE, related_name="audio_samples"
    )
    captured_at = models.DateTimeField(default=timezone.now)
    duration_seconds = models.FloatField(null=True, blank=True)
    sample_rate = models.PositiveIntegerField(null=True, blank=True)
    channels = models.PositiveSmallIntegerField(null=True, blank=True)
    audio_format = models.CharField(max_length=50, blank=True)

    class Meta:
        ordering = ["-captured_at", "-id"]
        verbose_name = _("Audio Sample")
        verbose_name_plural = _("Audio Samples")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return _("Audio sample for %(sample)s") % {"sample": self.sample}

    @staticmethod
    def _resolve_path(sample: ContentSample) -> Path:
        file_path = Path(sample.path)
        if not file_path.is_absolute():
            file_path = settings.LOG_DIR / file_path
        return file_path

    @classmethod
    def build_metadata(cls, sample: ContentSample) -> dict[str, object]:
        duration_seconds: float | None = None
        sample_rate: int | None = None
        channels: int | None = None
        audio_format = ""
        file_path = cls._resolve_path(sample)
        if file_path.exists():
            audio_format = (mimetypes.guess_type(file_path.name)[0] or "").lower()
            if file_path.suffix.lower() == ".wav":
                try:
                    with wave.open(str(file_path), "rb") as wav_file:
                        sample_rate = wav_file.getframerate()
                        channels = wav_file.getnchannels()
                        frame_count = wav_file.getnframes()
                        if sample_rate:
                            duration_seconds = frame_count / float(sample_rate)
                except wave.Error as exc:  # pragma: no cover - best-effort metadata
                    logger.warning(
                        "Could not read audio metadata from %s: %s", file_path, exc
                    )
        return {
            "captured_at": sample.created_at,
            "duration_seconds": duration_seconds,
            "sample_rate": sample_rate,
            "channels": channels,
            "audio_format": audio_format,
        }

    def get_data_uri(self) -> str | None:
        file_path = self._resolve_path(self.sample)
        if not file_path.exists():
            return None
        mime = self.audio_format or "audio/wav"
        data = file_path.read_bytes()
        encoded = base64.b64encode(data)
        return f"data:{mime};base64,{encoded.decode('ascii')}"
