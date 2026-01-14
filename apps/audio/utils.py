from __future__ import annotations
from datetime import datetime
from pathlib import Path
import logging
import shutil
import subprocess
import uuid

from django.conf import settings

from apps.content.utils import save_content_sample
from apps.content.models import ContentSample

from .models import AudioSample, PCM_PATH, RecordingDevice

logger = logging.getLogger(__name__)

AUDIO_DIR = settings.LOG_DIR / "audio"


def record_microphone_sample(
    duration_seconds: int = 6,
    *,
    sample_rate: int = 16_000,
    channels: int = 1,
    device_identifier: str | None = None,
    pcm_path: Path = PCM_PATH,
) -> Path:
    """Record audio from the default microphone and return the saved path."""

    tool_path = shutil.which("arecord")
    if not tool_path:
        raise RuntimeError("arecord is not available")
    if device_identifier is None:
        preferred_device = RecordingDevice.preferred_device(pcm_path=pcm_path)
        device_identifier = preferred_device.identifier if preferred_device else None
    alsa_device = (
        RecordingDevice.identifier_to_alsa_device(device_identifier)
        if device_identifier
        else None
    )
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.utcnow()
    unique_suffix = uuid.uuid4().hex
    filename = AUDIO_DIR / f"{timestamp:%Y%m%d%H%M%S}-{unique_suffix}.wav"
    try:
        command = [
            tool_path,
            "-q",
        ]
        if alsa_device:
            command.extend(["-D", alsa_device])
        command.extend(
            [
                "-f",
                "S16_LE",
                "-r",
                str(sample_rate),
                "-c",
                str(channels),
                "-d",
                str(duration_seconds),
                str(filename),
            ]
        )
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=duration_seconds + 5,
        )
    except Exception as exc:  # pragma: no cover - depends on audio stack
        logger.error("Failed to invoke %s: %s", tool_path, exc)
        raise RuntimeError(f"Audio capture failed: {exc}") from exc
    if result.returncode != 0:
        error = (result.stderr or result.stdout or "Audio capture failed").strip()
        logger.error("%s exited with %s: %s", tool_path, result.returncode, error)
        raise RuntimeError(error)
    if not filename.exists():
        logger.error("Audio sample file %s was not created", filename)
        raise RuntimeError("Audio capture failed")
    return filename


def save_audio_sample(
    path: Path,
    *,
    node=None,
    method: str = "",
    transaction_uuid=None,
    user=None,
    link_duplicates: bool = False,
):
    """Save audio file info if not already recorded."""

    sample = save_content_sample(
        path=path,
        kind=ContentSample.AUDIO,
        node=node,
        method=method,
        transaction_uuid=transaction_uuid,
        user=user,
        link_duplicates=link_duplicates,
        duplicate_log_context="audio sample",
    )
    if not sample:
        return None

    metadata = AudioSample.build_metadata(sample)
    audio_sample, created = AudioSample.objects.get_or_create(
        sample=sample,
        defaults=metadata,
    )
    if not created:
        updates: dict[str, object] = {}
        for field, value in metadata.items():
            if getattr(audio_sample, field) != value:
                setattr(audio_sample, field, value)
                updates[field] = value
        if updates:
            audio_sample.save(update_fields=list(updates.keys()))
    return sample


def has_audio_capture_device(*, pcm_path: Path = PCM_PATH) -> bool:
    """Return ``True`` when a recording device is available."""

    return RecordingDevice.has_recording_device(pcm_path=pcm_path)
